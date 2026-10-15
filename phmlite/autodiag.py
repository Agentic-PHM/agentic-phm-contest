"""报警自动唤起诊断（任务②）：severe 报警去重后自动发起 AGH 非交互诊断会话。

链路：/api/device 采样落 severe 报警 → observe()（恢复重置 + 同一故障去重）
→ 后台线程健康检查 AGH（4178 serve 在线 = daemon 在线）→ 通过共享 agh_runtime.run_prompt 调用。
AGH 运行失败由诊断状态机处理。
状态机与播报事件经 /api/autodiag/status 暴露，吉祥物据此第一人称播报。

配置（环境变量，缺省即可用）：PHM_AUTODIAG=0 关闭；AGH_DIR / AGH_HOME / PHM_AGH_PROFILE /
AGH_PORT / AUTODIAG_BUDGET（秒）。
"""
import json
import os
import threading
import time
import urllib.request
from uuid import uuid4
from datetime import datetime
from pathlib import Path

from phmlite.agh_runtime import AGHError, probe_provider, run_prompt
from phmlite.operation_mode import (OperationModeError, get_mode, record_provider_probe)

POINT_CN = {"DE": "驱动端", "NDE": "非驱动端"}
# P0 问题2：报告存在只是必要条件，不是"诊断成功"。产出报告但闭环/证据未达标的 run
# 不再记 done，而是如实降级为下面两个状态。
PARTIAL = "partial"                        # 诊断与证据成立，闭环缺件（工单缺失）
VERIFICATION_FAILED = "verification_failed"  # 证据链本身不成立（证据结构/工具调用/关联失败）
# 已产出报告的终态：占用去重键，避免同一故障反复重跑
ARTIFACT_STATES = ("done", PARTIAL, VERIFICATION_FAILED)
# 未产出产物的终态：不占去重键，环境修复后重新注入应能再次唤起
TERMINAL = ("done", "failed", PARTIAL, VERIFICATION_FAILED)
MIN_TOOL_CALLS = 5  # 完整诊断至少的工具调用次数（与 export_evidence.py 门禁同源）
#: 取证命令前缀：`-f` 让 HTTP≥400 时 curl 退出非零（审查 P1 问题5）——
#: 只用 `-s` 时接口 503 也返回 0，工具层 isError=false 会被误当成"业务成功"。
_CURL = "curl -sS -f"
EVIDENCE_KEYS = ("step", "tool", "key_numbers", "supports")
RETEST_REQUIRED = 3  # 复测判据：连续 3 个采样无严重报警（与报警"连续3点越限"判据对称）
# P1 问题5：复测还必须跨过一段时间窗口——只数样本的话，采样密时几秒就能"复测通过"。
# 演示默认 30s；真实运维应显著更长（小时级），由 AUTODIAG_RETEST_WINDOW 调整。
RETEST_WINDOW_S = 30.0
RETEST_KIND_SIM = "sim_recovery"     # 关单依据：仿真恢复验证通过（未验证实际维修）
RETEST_KIND_MANUAL = "manual"        # 关单依据：人工处置确认
_FAILURE_TEXT = {
    "runtime_unavailable": "AGH 运行时离线",
    "runtime_missing": "找不到 AGH 运行时",
    "provider_unavailable": "Agnes Key 或模型不可用",
    "timeout": "AGH 模型调用超时",
    "cli_failed": "AGH 会话执行失败",
    "invalid_response": "AGH 返回格式无效",
    "missing_report": "AGH 会话结束但没有提交本次诊断报告",
    "evidence_invalid": "诊断证据结构不合规",
    "task_missing": "本次诊断没有关联工单",
    "tool_calls_insufficient": "AGH 工具调用次数不足",
    "linkage_failed": "业务记录与 AGH 会话无法互相指认",
    "index_failed": "执行索引无法建立",
    "index_write_failed": "执行索引落盘失败",
}


def _env(key, default):
    return os.environ.get(key, default)


def _default_index_provider(run_id):
    """执行索引来源（P0 问题2）：从 AGH 会话库定位 run_id 的 turn 窗口。
    与 runner / provider_probe 同样可注入，离线单测才能验证核验门禁本身。"""
    from phmlite import agh_evidence, agh_runtime

    return agh_evidence.index_for_run(agh_runtime.home(), run_id)


def _key_numbers_ok(numbers):
    """key_numbers 是否提供了实测数值支撑。

    2026-10-13 实测：此前只接受**非空数组**，而智能体很自然地写成对象
    `{"kurtosis":10.497,"rms":6.7}`——校验器判它"为空"并返回 400，报告直接落不了库
    （真机验收因此 FAIL，而提示词里根本没规定形状）。两种写法都是合法的证据载体：
    数组（"名称=数值" 字符串）与对象（名称→数值）。"""
    if isinstance(numbers, dict):
        return any(v is not None and v != "" for v in numbers.values())
    if isinstance(numbers, list):
        return bool(numbers)
    return False


def parse_evidence_steps(evidence_json):
    """证据结构校验：必须是"至少一条"的数组，每条含 step/tool/key_numbers(有实测值)/supports。

    报告接口与收尾门禁共用同一判据——这样"允许提交"与"算作成功"标准一致，
    evidence_json="{}" 既不接受为报告，也不接受为成功依据。返回 (steps, problem)。"""
    if not isinstance(evidence_json, str) or not evidence_json.strip():
        return None, "证据为空"
    try:
        data = json.loads(evidence_json)
    except ValueError:
        return None, "证据不是合法 JSON"
    if not isinstance(data, list) or not data:
        return None, "证据必须是非空数组"
    for idx, step in enumerate(data, start=1):
        if not isinstance(step, dict):
            return None, f"证据第 {idx} 条不是对象"
        missing = [k for k in EVIDENCE_KEYS if k not in step]
        if missing:
            return None, f"证据第 {idx} 条缺字段 {'/'.join(missing)}"
        if not _key_numbers_ok(step.get("key_numbers")):
            # 错误信息要说清**期望的形状**：智能体靠它自我修正（此前只说"为空"，
            # 而它给的是对象，看不出问题在哪，两次重试都照原样再发一次）
            return None, (f"证据第 {idx} 条 key_numbers 未提供实测数值："
                          f"请给非空数组（如 [\"DE.kurtosis=10.49\"]）或对象（如 {{\"kurtosis\": 10.49}}）")
    return data, None


class AutoDiag:
    """severe 报警 → 自动唤起 AGH 诊断会话；失败时显式收尾，不造诊断结果。"""

    def __init__(self, db, data_dir, runner=None, provider_probe=None, index_provider=None):
        self.db = db
        self.data_dir = Path(data_dir)
        self.log_dir = self.data_dir / "autodiag"
        self.enabled = _env("PHM_AUTODIAG", "1") != "0"
        self.agh_port = int(_env("AGH_PORT", "4178"))
        # 硬杀预算（非目标值）：实测分布 28.7~73.2s，历史离群 123s（10-06），240s 留足波动余量
        self.budget = float(_env("AUTODIAG_BUDGET", "240"))
        self.retry_backoff = float(_env("AUTODIAG_RETRY_BACKOFF", "8"))  # 重试退避：限流场景立即重试无意义
        self.retest_window_s = float(_env("AUTODIAG_RETEST_WINDOW", str(RETEST_WINDOW_S)))
        # P1 问题7：受限 Shell 模式——撤掉 AGH session yolo，命令必须落在 command_policy 白名单内。
        # 默认关闭（保持现状稳定）；开启时 setup_agh_runtime.py 必须同步改成非 yolo 模式。
        self.restricted_shell = _env("PHM_AGH_RESTRICTED_SHELL", "0") == "1"
        self._runner = runner or self._run_agh  # 测试可注入假 runner
        self._provider_probe = provider_probe or probe_provider
        # 执行索引来源：测试注入假索引，避免离线单测依赖真实 AGH 会话库
        self._index_provider = index_provider or _default_index_provider
        self._lock = threading.RLock()
        self._events = []
        self._seq = 0
        self._reset()

    def _reset(self):
        # 留档上一轮三态终值（P0 问题3：闭环完成归位后，diagnosis/task 阶段结论仍可查）
        task_id = getattr(self, "task_id", None)
        report_id = getattr(self, "report_id", None)
        task_status = None
        if task_id:
            task = self.db.get_task(task_id)
            task_status = task["status"] if task else None
        self.last_phases = {"run_id": getattr(self, "run_id", None),
                            "diagnosis_done": report_id is not None,
                            "task_created": task_id is not None,
                            "task_closed": task_status == "closed",
                            # P0 问题2：归位后仍要能查上一轮到底算 done 还是只到 partial
                            "terminal_state": getattr(self, "state", None),
                            "verification": getattr(self, "verification", None)}
        self.state = "idle"          # idle|dispatching|running|done|partial|verification_failed|failed
        self.mode = None             # agh
        self.run_id = None
        self.alarm = None
        self.fault = None
        self.report_id = None
        self.task_id = None
        self.failure_reason = None
        self.verification = None      # 完整性核验明细（P0 问题2：done 的判据）
        self.evidence_index = None    # 执行索引文件路径（run_id ↔ AGH 会话，P0 问题2）
        self._retest_count = 0        # 复测计数：fault=none 后连续无严重报警的采样次数
        self._retest_started_at = None  # 复测窗口起点（单调时钟），用于"跨时间窗"判据
        self._retest_pending = False  # 复测进行中（工单待关）
        self.started_at = None
        self.finished_at = None
        self._fault_key = None       # 已唤起过的故障签名（去重，恢复后清空）
        self._events = []

    # ---- 对外：/api/device 每次采样后调用 ----
    def observe(self, scenario, severe_alarms, measured=None):
        """恢复复测关单（fault=none 且诊断已完成 → 连续 N 次无严重越限自动关单）
        + severe 报警去重唤起。非阻塞。

        `measured`：本次采样的**逐测点实测结果**（含 level）。审查 P0 问题3 指出复测此前读的是
        `severe_alarms`——那只是"本次新建的报警行"，而报警要连续 ACCURING_NUM 点才落行；
        单次严重越限的样本已经异常却不在列表里，会被复测当成正常并提前关单。
        因此复测一律以实测 level 为准，`severe_alarms` 仅作补充。
        """
        if not self.enabled:
            return
        try:
            if get_mode(self.data_dir) == "manual":
                return
        except OperationModeError:
            self._emit("运行模式配置异常，自动诊断已暂停，请检查系统设置。", announce=False)
            return
        fault = scenario.get("fault") or {}
        with self._lock:
            if fault.get("type", "none") == "none":
                self._observe_recovery_locked(severe_alarms, measured)
                return
            # 故障活动中：复测计数清零；若复测进行中被重新注入，明确中止并允许再次唤起
            if self.state == "done" and self._retest_pending:
                self._emit("故障重新注入，本次复测中止；工单保持待处理。", announce=False)
                self._retest_pending = False
                self._retest_count = 0
                self._retest_started_at = None
                self._fault_key = None  # 处置未闭环，允许同一故障再次唤起诊断
            self._retest_count = 0
            self._retest_started_at = None
            if self.state in ("dispatching", "running"):
                return  # 会话进行中，不打断
            key = fault.get("type")
            # 同一故障只唤起一次——对"已产出报告"的终态（done/partial/verification_failed）生效，
            # 否则同一故障会反复重跑；failed 未产出产物，不占去重键，
            # 环境修复（如 AGH 重启）后重新注入应能再次唤起，否则一次失败永久卡死验收/演示
            if self.state in ARTIFACT_STATES and key == self._fault_key:
                return
            if not severe_alarms:
                return
            alarm = severe_alarms[0]
            self._fault_key = key
            self.state = "dispatching"
            self.run_id = "auto-" + datetime.now().strftime("%m%d-%H%M%S") + "-" + uuid4().hex[:8]
            self.alarm = {k: alarm[k] for k in ("point", "level", "kpi", "value", "ts")}
            self.fault = {"type": fault.get("type"), "severity": fault.get("severity")}
            self.started_at = datetime.now().isoformat(timespec="seconds")
            self._emit(f"检测到{POINT_CN.get(alarm['point'], alarm['point'])}振动严重越限"
                       f"（{alarm['kpi']}={alarm['value']:.2f}），正在检查 AGH 运行时与 Agnes 模型配置…")
        threading.Thread(target=self._dispatch, daemon=True).start()

    def _observe_recovery_locked(self, severe_alarms, measured=None):
        """fault=none（调用方已持锁）：对已完成诊断的 run 做**复测**，满足判据才自动关单。

        P1 问题5：此前只要 `fault.type=none` 就自增计数——`severe_alarms` 传进来了却从未被读，
        docstring 说的"连续 N 次**无严重报警**"实际没实现。于是"解除仿真注入"被当成了
        "设备已修好"。现在复测判据是：
          ① 本次采样确实没有严重越限（**读实测 level**，而不是只看场景开关或新建报警行）；
          ② 连续 RETEST_REQUIRED 个采样；
          ③ 跨过 retest_window_s 的时间窗（只数样本的话，采样密时几秒就能"通过"）。
        关单**依据**记为 sim_recovery，备注里写明未验证实际维修——仿真恢复 ≠ 维修完成。

        审查 P0 问题3：判据①的"实测"来自 `measured`（逐测点 level）。仅当调用方没有提供
        实测数据（历史调用/单测）时才退回只看 `severe_alarms`。

        只有 done 才允许自动关单（P0 问题2）：partial / verification_failed 说明这次结论
        未通过完整性核验，自动写"复测通过"等于替一次没核验过的诊断背书。"""
        if not (self.state == "done" and self.task_id):
            if self.state in TERMINAL:
                self._reset()
            return
        task = self.db.get_task(self.task_id)
        if not (task and task["status"] == "open"):
            self._reset()  # 工单已关闭或不存在：无事可复测，归位
            return

        # 实测优先：任一测点仍为 severe 即视为未恢复。measured=None 时退回旧口径。
        measured_severe = [p.get("name") for p in (measured or []) if p.get("level") == "severe"]
        still_severe = bool(measured_severe) or (measured is None and bool(severe_alarms))
        if still_severe:
            # 测量仍然严重越限：无论场景开关怎么设、无论是否已新建报警行，都不许计入复测
            if self._retest_pending:
                self._emit("复测期间实测仍为严重越限，复测计数清零；工单保持待处理。", announce=False)
            self._retest_count = 0
            self._retest_started_at = None
            self._retest_pending = False
            return

        now = time.monotonic()
        if self._retest_count == 0:
            self._retest_started_at = now
        self._retest_count += 1
        self._retest_pending = True
        elapsed = now - (self._retest_started_at or now)
        if self._retest_count < RETEST_REQUIRED or elapsed < self.retest_window_s:
            return  # 复测进行中：样本数或时间窗未达，保持 done 等待后续采样

        closed = self.db.close_task(
            self.task_id, kind=RETEST_KIND_SIM,
            note=(f"仿真恢复验证通过（复测，未验证实际维修）：解除仿真注入后连续 "
                  f"{self._retest_count} 个采样、跨 {elapsed:.0f}s 无严重报警。"
                  f"真实运维需人工维修确认后授权关单。"))
        self._retest_pending = False
        self._retest_count = 0
        self._retest_started_at = None
        self._reset()
        if closed:
            # 先归位再播报：_reset 会清事件列表，关单播报必须让吉祥物看得到
            self._emit(f"仿真恢复验证通过：连续 {RETEST_REQUIRED} 个采样、跨 {elapsed:.0f}s "
                       f"无严重报警，工单#{closed['id']}已关闭（未验证实际维修）。")

    # ---- 状态与播报事件 ----
    def _emit(self, text, announce=True):
        with self._lock:
            self._seq += 1
            self._events.append({"seq": self._seq,
                                 "ts": datetime.now().isoformat(timespec="seconds"),
                                 "text": text, "announce": announce})
            self._events = self._events[-20:]

    def status(self):
        """对外状态——不含 fault 真值（self.fault 仅供内部留档，公网可见面与诊断智能体同权）。
        phases 三态区分（P0 问题3）：诊断完成 ≠ 工单创建 ≠ 工单关闭，文案不得混用。"""
        task_status = None
        if self.task_id:
            task = self.db.get_task(self.task_id)
            task_status = task["status"] if task else None
        with self._lock:
            phases = {"diagnosis_done": self.report_id is not None,
                      "task_created": self.task_id is not None,
                      "task_closed": task_status == "closed"}
            verification = self.verification
            if self.state == "idle" and getattr(self, "last_phases", None):
                phases = dict(self.last_phases)  # 归位后回显上一轮终值
                verification = self.last_phases.get("verification")
            return {"enabled": self.enabled, "state": self.state, "mode": self.mode,
                    "run_id": self.run_id, "alarm": self.alarm,
                    "report_id": self.report_id, "task_id": self.task_id,
                    "task_status": task_status,
                    "phases": phases,
                    # P0 问题2：把"为什么不是 done"逐项摊开，评委可核查而非只能相信结论
                    "verification": verification,
                    "retest": {"count": self._retest_count, "required": RETEST_REQUIRED,
                               "pending": self._retest_pending,
                               # P1 问题5：复测还要跨时间窗，把窗口与已跨时长一并外露供核查
                               "window_s": self.retest_window_s,
                               "elapsed_s": (round(time.monotonic() - self._retest_started_at, 1)
                                             if self._retest_started_at else 0.0)},
                    "failure_reason": self.failure_reason,
                    "evidence_index": self.evidence_index,
                    "started_at": self.started_at, "finished_at": self.finished_at,
                    "events": list(self._events)}

    # ---- 派发：先验证 AGH daemon 与 Agnes provider，再开始会话 ----
    def _dispatch(self):
        self._emit("正在检查 AGH 运行时和 Agnes 模型配置…")
        prompt = self._prompt()
        try:
            if get_mode(self.data_dir) == "manual":
                with self._lock:
                    self.state = "failed"
                    self.mode = "manual"
                    self.failure_reason = "manual_mode"
                    self.finished_at = datetime.now().isoformat(timespec="seconds")
                self._emit("当前为人工备用模式，智能体诊断未启动。")
                return
            self._health_check()
            self._provider_probe()
            record_provider_probe(True)
            with self._lock:
                self.mode = "agh"
                self.state = "running"
            self._emit("AGH 与 Agnes 模型已验证，我开始自主取证诊断…")
            self._run_with_retry(prompt)
            self._settle()
        except Exception as exc:  # noqa: BLE001 —— 失败只收尾，不生成规则诊断产物
            category = getattr(exc, "category", "agh_failed")
            record_provider_probe(False, category)
            cleanup_failed = False
            try:
                self.db.delete_agent_run_artifacts(self.run_id)
            except Exception:  # noqa: BLE001 —— 清理失败也明确提示人工核查
                cleanup_failed = True
            with self._lock:
                self.state = "failed"
                self.mode = "agh"
                self.failure_reason = "artifact_cleanup_failed" if cleanup_failed else category
                self.finished_at = datetime.now().isoformat(timespec="seconds")
            reason = _FAILURE_TEXT.get(category, "AGH 智能体会话失败")
            suffix = "部分产物清理失败，请检查本次 run 记录。" if cleanup_failed else "请人工复核。"
            self._emit(f"{reason}，AGH 智能体诊断失败，{suffix}")
            return

    def _run_with_retry(self, prompt):
        """serve 刚启动的会话竞态 / Agnes 限流（overloaded 空回复）：无产物落库时重试一次。
        重试前退避 8s——限流场景立即重试只会再次撞墙。"""
        for attempt in (1, 2):
            try:
                self._runner(prompt, self.run_id)
                return
            except AGHError as exc:
                if attempt == 2 or any(r['run_id'] == self.run_id for r in self.db.report_list()) or \
                        any(t.get("run_id") == self.run_id for t in self.db.task_list()):
                    raise  # 已有部分产物，重试会造成重复，交由失败收尾清理
                self._emit(f"AGH 会话异常（{exc.category}），{self.retry_backoff:.0f} 秒后自动重试一次…",
                           announce=False)
                time.sleep(self.retry_backoff)

    def _verify_run(self, report, tasks):
        """完整性核验（P0 问题2）：报告存在只是必要条件。返回 verification 明细。

        核验五项：报告存在、证据结构合规、工单存在、AGH 工具调用真实完成、
        业务记录与 AGH 会话双向关联成功。任一不达标即不得记 done。"""
        checks = {
            "report": True,
            "evidence_structure": False, "evidence_problem": None,
            "task_linked": bool(tasks),
            "index_built": False, "tool_calls": None, "tool_calls_ok": False,
            "linkage_ok": False, "linkage_errors": [],
            "problems": [], "messages": [],
        }

        _steps, problem = parse_evidence_steps(report.get("evidence"))
        checks["evidence_structure"] = problem is None
        checks["evidence_problem"] = problem
        if problem:
            checks["problems"].append("evidence_invalid")
            checks["messages"].append(f"证据结构不合规（{problem}）")

        if not checks["task_linked"]:
            checks["problems"].append("task_missing")
            checks["messages"].append("本次运行没有关联工单")

        try:
            from phmlite import agh_evidence

            index = self._index_provider(self.run_id)
            checks["index_built"] = True
            checks["tool_calls"] = index["checks"]["tool_call_count"]
            checks["tool_calls_ok"] = checks["tool_calls"] >= MIN_TOOL_CALLS
            if not checks["tool_calls_ok"]:
                checks["problems"].append("tool_calls_insufficient")
                checks["messages"].append(
                    f"AGH 工具调用仅 {checks['tool_calls']} 次（完整诊断应≥{MIN_TOOL_CALLS}）")
            # 审查 P1 问题5：工具成功 ≠ 业务成功。写接口必须留下**明确成功**的工具调用记录；
            # 提示词已要求取证命令带 `-f`（HTTP≥400 → curl 退出非零 → 工具报错），
            # 因此这一项能挡住"接口 503 但工具层看起来没事"的情况。
            checks["tool_error_count"] = index["checks"].get("tool_error_count")
            checks["write_calls"] = index["checks"].get("write_calls")
            checks["write_calls_ok"] = bool(index["checks"].get("write_calls_ok"))
            if not checks["write_calls_ok"]:
                checks["problems"].append("write_call_unverified")
                checks["messages"].append(
                    "报告/工单写接口没有成功完成的 AGH 工具调用记录（HTTP 失败或缺少结果事件）")
            errors = agh_evidence.validate_linkage(index, [report], tasks)
            checks["linkage_errors"] = errors
            checks["linkage_ok"] = not errors
            if errors:
                checks["problems"].append("linkage_failed")
                checks["messages"].append("业务记录与 AGH 会话无法互相指认：" + "；".join(errors))
            checks["index"] = index
        except Exception as exc:  # noqa: BLE001 —— 索引建不起来本身就是核验失败
            checks["problems"].append("index_failed")
            checks["messages"].append(f"执行索引无法建立（{type(exc).__name__}），证据链需人工补档")

        # 只有"闭环缺件"（工单缺失）而证据与执行链都成立 → partial；其余 → verification_failed
        checks["closure_gap_only"] = checks["problems"] == ["task_missing"]
        checks["ok"] = not checks["problems"]
        return checks

    def _settle(self):
        """AGH 会话结束后按智能体实际落库结果收尾。

        P0 问题2：不再"查到报告即 done"。done 要求报告 + 工单 + 证据结构 + AGH 工具调用 +
        业务关联全部成立；否则降级 partial / verification_failed，绝不显示全部完成。"""
        rows = [r for r in self.db.report_list() if r["run_id"] == self.run_id]
        if not rows:
            with self._lock:
                self.finished_at = datetime.now().isoformat(timespec="seconds")
                self.db.delete_agent_run_artifacts(self.run_id)
                self.state = "failed"
                self.failure_reason = "missing_report"
                self._emit("诊断会话结束但未见报告落库，请人工复核。")
            return

        rep = rows[0]
        tasks = [t for t in self.db.task_list()
                 if t.get("run_id") == self.run_id and t.get("source") == "agent"]
        verification = self._verify_run(rep, tasks)
        # 执行索引只在内部用于落盘：status 每 2s 轮询，不应把整份索引挂在对外面
        index = verification.pop("index", None)

        with self._lock:
            self.finished_at = datetime.now().isoformat(timespec="seconds")
            self.report_id = rep["id"]
            self.mode = "agh"
            self.task_id = tasks[0]["id"] if tasks else None
            self.verification = verification
            if verification["ok"]:
                self.state = "done"
                self.failure_reason = None
                self._emit(f"诊断完成：{rep['conclusion']}（自评置信度{rep['confidence'] * 100:.0f}%），"
                           f"报告#{rep['id']}已落库" + (f"，工单#{self.task_id}已生成。" if self.task_id else "。"))
            else:
                self.state = PARTIAL if verification["closure_gap_only"] else VERIFICATION_FAILED
                self.failure_reason = verification["problems"][0]
                head = "诊断报告已落库，但只完成到部分闭环" if self.state == PARTIAL \
                    else "诊断报告已落库，但未通过完整性核验"
                self._emit(f"{head}：{'；'.join(verification['messages'])}。")

        if index is not None:
            self._write_evidence_index(index)

    def _write_evidence_index(self, index):
        """执行索引（P0 问题2）落盘：<run_id>.index.json。
        索引已在核验阶段建立；这里只负责写文件，写失败要撤销 done——证据链断链不得算完成。"""
        try:
            # 落盘目录自建：此前依赖 _run_agh 先写过提示词日志，注入假 runner 时目录不存在，
            # 索引写不进去却仍记 done（核验门禁把这个隐患暴露出来了）
            self.log_dir.mkdir(parents=True, exist_ok=True)
            path = self.log_dir / f"{self.run_id}.index.json"
            path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
            with self._lock:
                self.evidence_index = str(path)
            self._emit(f"执行索引已生成：AGH 会话 turn {index['turns']}，"
                       f"工具调用 {index['checks']['tool_call_count']} 次，模型 {index['model']}。",
                       announce=False)
        except Exception as exc:  # noqa: BLE001 —— 落盘失败必须撤销成功状态
            with self._lock:
                self.evidence_index = None
                if self.state == "done":
                    self.state = VERIFICATION_FAILED
                    self.failure_reason = "index_write_failed"
                    self.verification = dict(self.verification or {}, ok=False)
                    self.verification.setdefault("problems", []).append("index_write_failed")
                    self.verification.setdefault("messages", []).append(
                        f"执行索引落盘失败（{type(exc).__name__}）")
            self._emit(f"执行索引落盘失败（{type(exc).__name__}）：证据链需人工补档，"
                       f"本次不再计为完成；可用 scripts/export_evidence.py 重试。")

    def _prompt(self):
        """诊断提示词——与 diagnosis 技能同一取证流程（趋势+FFT+包络谱+三判据交叉验证），
        只允许包含报警与取证口径，禁止出现注入真值（fault.type/severity），
        这是"智能体独立诊断"命题的审计红线（P0）：泄漏一行，全部结论失去证明力。"""
        a = self.alarm
        rid = self.run_id
        if self.restricted_shell:
            return self._restricted_prompt(a, rid)
        return (
            f"P-101 调相机严重报警：{POINT_CN.get(a['point'], a['point'])}测点 {a['kpi']}={a['value']:.2f}。"
            f"你是自主诊断智能体，无人值守，不要提问，全程中文，控制在 10 次工具调用内，按以下顺序取证："
            f"① shell 执行 {_CURL} \"http://127.0.0.1:8181/api/device\" 确认测点等级（时域：峭度/RMS）；"
            f"② shell 执行 {_CURL} \"http://127.0.0.1:8181/api/trend?point={a['point']}&kpi=rms&hours=24\" "
            f"看趋势是否恶化；"
            f"③ shell 执行 {_CURL} \"http://127.0.0.1:8181/api/spectrum?point={a['point']}&type=fft&top=12\" "
            f"看 1×/2× 转频幅值（不对中/不平衡判据）；"
            f"④ shell 执行 {_CURL} \"http://127.0.0.1:8181/api/spectrum?point={a['point']}&type=envelope&top=12\" "
            f"取包络谱；"
            f"⑤ shell 执行 {_CURL} \"http://127.0.0.1:8181/api/charfreq\" 取理论特征频率，与④谱峰做 ±3Hz 比对；"
            f"⑥ 交叉验证：时域峭度（①）、频域特征峰（③④⑤）、趋势变化（②）三项至少两项一致才可下结论，"
            f"结论中写明支持结论的判据；证据不足时明确列出还缺什么并继续取证，不许猜；"
            f"⑦ 提交报告（**中文直接写，交给 curl 编码**，不要自己写百分号转义）："
            f"{_CURL} -G \"http://127.0.0.1:8181/api/report/submit\" "
            f"--data-urlencode \"run_id={rid}\" --data-urlencode \"equip=P-101\" "
            f"--data-urlencode \"conclusion=<中文结论文本，必须来自你的取证判断，"
            f"如：驱动端轴承存在缺陷，建议停机检查>\" "
            f"--data-urlencode \"confidence=<0~1>\" "
            f"--data-urlencode \"component_id=<部件id，如bearing_de>\" "
            f"--data-urlencode \"evidence_json=<JSON数组原文，每步含 step/tool/key_numbers/supports，"
            f"覆盖时域/频域/趋势三类证据；key_numbers 写实测数值，"
            f"数组（[\"DE.kurtosis=10.50\"]）或对象（{{\"kurtosis\":10.50}}）都可以，但不能为空>\";"
            f"——注意 conclusion 参数是结论文本，绝不能只填数字；"
            f"自己手写百分号编码极易写错字节（实测把「轴承」编成了「轴才」），一律交给 curl；"
            f"⑧ 生成工单：{_CURL} -G \"http://127.0.0.1:8181/api/task/create\" "
            f"--data-urlencode \"title=<含停机检查>\" --data-urlencode \"desc=<中文依据>\" "
            f"--data-urlencode \"level=severe\" --data-urlencode \"source=agent\" "
            f"--data-urlencode \"run_id={rid}\"；"
            f"⑨ 最后只输出一行中文小结：结论+置信度+已落库。"
            f"取证命令必须带 `-f`（HTTP 错误要让工具报错，别用会把错误当成功的调用方式），"
            f"并在提交后核对接口返回的 id 与 run_id 是否一致；"
            f"引用趋势时必须区分数据来源：/api/trend 的每个点带 origin，"
            f"`origin=seed` 是**模拟历史曲线**、`origin=live` 才是本次运行的真实采样；"
            f"不得把模拟历史描述成设备过去 24 小时的真实劣化，"
            f"应表述为「故障注入后出现的短时突变」或注明该段为仿真历史；"
            f"取证只允许使用上述接口；禁止访问 /api/scenario/*（仿真控制通道，与诊断无关）、"
            f"/api/agent/*（人机对话通道）与 /api/autodiag/*（调度内部状态）。"
        )

    def _restricted_prompt(self, a, rid):
        """受限 Shell 模式（P1 问题7）：撤掉 session yolo 后，命令必须落在
        `approval.command_policy` 白名单内，否则无人值守下会被直接拒绝。

        因此命令形态由 `phmlite.agh_policy` 统一生成（绝对路径 curl；写接口用
        `-G --data-urlencode` 而不是让模型自己拼 PowerShell 编码函数），并在下发前逐条自检
        ——白名单与提示词一旦漂移，这里会立刻失败而不是等到演示时诊断卡死。"""
        from phmlite import agh_policy as pol

        point = a["point"]
        device = pol.get_command(f"{pol.API_BASE}/api/device")
        trend = pol.get_command(f"{pol.API_BASE}/api/trend?point={point}&kpi=rms&hours=24")
        fft = pol.get_command(f"{pol.API_BASE}/api/spectrum?point={point}&type=fft&top=12")
        envelope = pol.get_command(f"{pol.API_BASE}/api/spectrum?point={point}&type=envelope&top=12")
        charfreq = pol.get_command(f"{pol.API_BASE}/api/charfreq")
        submit = pol.write_command("/api/report/submit", {
            "run_id": rid, "equip": "P-101",
            "conclusion": "<中文结论文本，结论必须来自你的取证判断，如：驱动端轴承存在缺陷，建议停机检查>",
            "confidence": "<0~1>",
            "component_id": "<部件id，如bearing_de>",
            "evidence_json": "<JSON数组，每步含step/tool/key_numbers/supports，覆盖时域/频域/趋势三类证据；"
                             "key_numbers 写实测数值，数组或对象都可以但不能为空>",
        })
        task = pol.write_command("/api/task/create", {
            "title": "<含停机检查>", "desc": "<中文依据>",
            "level": "severe", "source": "agent", "run_id": rid,
        })

        # 自检：白名单必须覆盖提示词里的全部命令形态（占位符换成实参后校验）
        probes = [device, trend, fft, envelope, charfreq,
                  pol.write_command("/api/report/submit",
                                    {"run_id": rid, "equip": "P-101", "conclusion": "x",
                                     "confidence": "0.9", "component_id": "bearing_de",
                                     "evidence_json": "[{}]"}),
                  pol.write_command("/api/task/create",
                                    {"title": "t", "desc": "d", "level": "severe",
                                     "source": "agent", "run_id": rid})]
        for cmd in probes:
            problem = pol.verify_command(cmd)
            if problem:
                raise RuntimeError(f"受限 Shell 模式自检失败：{problem}")

        return (
            f"P-101 调相机严重报警：{POINT_CN.get(point, point)}测点 {a['kpi']}={a['value']:.2f}。"
            f"你是自主诊断智能体，无人值守，不要提问，全程中文，控制在 10 次工具调用内，按以下顺序取证："
            f"① shell 执行 {device} 确认测点等级（时域：峭度/RMS）；"
            f"② shell 执行 {trend} 看趋势是否恶化；"
            f"③ shell 执行 {fft} 看 1×/2× 转频幅值（不对中/不平衡判据）；"
            f"④ shell 执行 {envelope} 取包络谱；"
            f"⑤ shell 执行 {charfreq} 取理论特征频率，与④谱峰做 ±3Hz 比对；"
            f"⑥ 交叉验证：时域峭度（①）、频域特征峰（③④⑤）、趋势恶化（②）三项至少两项一致才可下结论，"
            f"结论中写明支持结论的判据；证据不足时明确列出还缺什么并继续取证，不许猜；"
            f"⑦ 提交报告：{submit}"
            f"——注意 conclusion 直接写中文文本，curl 会负责 URL 编码，绝不能只填数字；"
            f"⑧ 生成工单：{task}；"
            f"⑨ 最后只输出一行中文小结：结论+置信度+已落库。"
            f"本次只授权了上述绝对路径 curl 命令：其余任何 shell 命令都会被拒绝，不要尝试。"
            f"命令里的 `-f` 不可省略：HTTP 错误必须让工具报错，不要用会把错误当成功的调用方式；"
            f"中文参数一律直接写，交给 curl 编码（`--data-urlencode`），不要自己写百分号转义"
            f"——实测手写会把「轴承」编成「轴才」，结论变成乱码；"
            f"提交后核对接口返回的 id 与 run_id 是否一致。"
            f"引用趋势时必须区分数据来源：/api/trend 的每个点带 origin，"
            f"`origin=seed` 是**模拟历史曲线**、`origin=live` 才是本次运行的真实采样；"
            f"不得把模拟历史描述成设备过去 24 小时的真实劣化，"
            f"应表述为「故障注入后出现的短时突变」或注明该段为仿真历史；"
            f"取证只允许使用上述接口；禁止访问 /api/scenario/*（仿真控制通道，与诊断无关）、"
            f"/api/agent/*（人机对话通道）与 /api/autodiag/*（调度内部状态）。"
        )

    def _run_agh(self, prompt, run_id):
        """AGH CLI -p 非交互模式。提示词原文落盘供独立评分器审计。

        授权形态取决于运行时的配置（P1 问题7）：
          - 默认：便携运行时带 `session.setYolo(true)` 补丁，shell 免逐次审批（宽授权）；
          - `PHM_AGH_RESTRICTED_SHELL=1`：运行时改为 `approval.command_policy` 白名单，
            未命中命令在无人值守下被拒（受限授权，见 phmlite/agh_policy.py）。"""
        with self._lock:
            self.mode = "agh"
            self.state = "running"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{run_id}.log"
        log_path.write_text(f"PROMPT:\n{prompt}\n", encoding="utf-8")
        try:
            run_prompt(prompt, timeout=self.budget)
        except Exception as exc:  # noqa: BLE001
            # 2026-10-13：此前日志只写类别（"cli_failed"），把 AGHError 的消息整条丢掉——
            # 退出码和 stderr 摘要都在消息里，丢了他就无从排查（实测两次工具失效用例卡在这）。
            # 消息由 agh_runtime 做过凭据脱敏，安全类别仍单独保留供程序判断。
            category = getattr(exc, "category", "runtime_error")
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"AGH session failed ({category}): {exc}\n")
            raise
        with log_path.open("a", encoding="utf-8") as f:
            f.write("AGH session completed; details remain in the AGH audit log.\n")

    def _health_check(self):
        url = f"http://127.0.0.1:{self.agh_port}/"
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status != 200:
                    raise AGHError("runtime_unavailable", "AGH daemon not ready")
        except AGHError:
            raise
        except Exception:
            raise AGHError("runtime_unavailable", "AGH daemon is unavailable") from None
