# -*- coding: utf-8 -*-
"""盲测基准（P0 问题4）：隐藏真值评估诊断智能体，产出实测指标——混淆矩阵/准确率/
误报率/任务完成率/延迟分布，并给出规则算法基线对比。没有任何估计值，没跑出来的
指标在报告里标 not_measured。

用例集（6 类覆盖）：
  normal-baseline    正常运行                    → 期望：管线不触发；咨询不报故障
  noise-interference 噪声干扰（noise=0.35 无故障）→ 期望：同上（抗误报）
  bpfo-standard      BPFO 0.8 @2980rpm           → 期望：判 BPFO
  bpfi-standard      BPFI 0.8 @2980rpm           → 期望：判 BPFI
  bpfo-boundary-kurt 边界越限（BPFO 0.45：RMS 正常档 4.15 + 峭度严重档 8.03）
  bpfo-boundary-rms  边界越限（BPFO 0.88：RMS 7.30 贴严重线 7.1）
  bpfi-highrpm       BPFI 0.8 @2400rpm（变频，特征频率随转速变化）
  bpfo-tool-failure  BPFO 0.8 + 包络谱工具故障注入 → 期望：降级重试仍完成诊断

用法：D:/Python311/python.exe scripts/blind_benchmark.py [--out evidence/...]
前置：8181 已起（本脚本经 API 操作），4178 AGH 在线，操作台无口令差异。
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from phmlite.agh_runtime import run_prompt  # noqa: E402
from phmlite.dsp import characteristic_frequencies, envelope_spectrum, synthesize  # noqa: E402
from phmlite import operator_auth  # noqa: E402

BASE = "http://127.0.0.1:8181"
#: 控制面凭据：main() 里登录后填充（P1 问题4——不再用固定字符串 X-Operator: console）
OP = {}
FS, N = 16000, 16384
TRIGGER_TIMEOUT = 300      # 单用例诊断收敛上限（秒）
NORMAL_WINDOW_S = 40       # normal/noise 用例的误报观察窗（秒）
CONSULT_PROMPT = ("请自主检查 P-101 调相机当前运行状态：用 shell 执行 curl "
                  "\"http://127.0.0.1:8181/api/device\" 读取测点特征值，"
                  "如无异常请明确回答正常，如有异常给出诊断结论（何种缺陷、依据）。"
                  "无人值守，不要提问，全程中文，控制在 6 次工具调用内。")


def api(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def scenario(**kw):
    """按用例参数复位并写入场景（操作台通道）。

    `override`（可选）：直接指定送进报警判定的 rms/峭度，用于"机理本身能量不足以越严重限"的
    用例——例如基础松动 @severity 1.0 只到 notice 档，自动唤起（只由 severe 触发）根本不会启动，
    用例就跑了個空（2026-10-13 首轮实测踩到）。override 只改判定输入，波形仍是该机理合成的，
    因此频谱上的机理特征不受影响；对外接口会同时给出波形自身的 signal_rms 供核对。"""
    api("/api/scenario/set?key=fault.type&value=none", OP)
    api("/api/scenario/set?key=fault.severity&value=0", OP)
    api("/api/scenario/set?key=noise&value=%s" % kw.get("noise", 0.05), OP)
    api("/api/scenario/set?key=rpm&value=%s" % kw.get("rpm", 2980), OP)
    for key in ("rms", "kurtosis"):           # 每次复位都要清掉上一例的 override
        value = (kw.get("override") or {}).get(key)
        api("/api/scenario/set?key=override.%s&value=%s" % (key, "" if value is None else value), OP)
    if kw.get("fault") and kw["fault"] != "none":
        api("/api/scenario/set?key=fault.type&value=%s" % kw["fault"], OP)
        api("/api/scenario/set?key=fault.severity&value=%s" % kw.get("severity", 0.8), OP)
    api("/api/device?run_id=blind-apply", OP)


def chaos(on, target="spectrum"):
    """工具故障注入（4.2）：spectrum=工具报错；trend/charfreq=部分数据缺失。"""
    api(f"/api/testtools/chaos?target={target}&value={'on' if on else 'off'}", OP)


def login():
    """取控制面凭据并写入**本模块**的 OP（P1 问题4），返回该字典。

    其他脚本必须调用它，而不是各自 `OP.update(...)`：`run_e2e_case` / `_wait_idle` / `scenario`
    读的是本模块的 OP——验收脚本曾只填了自己的 OP，结果真实盲诊用例在第一步就 403
    （2026-10-13 真机验收实测踩到，离线单测覆盖不到这种跨模块的凭据传递）。"""
    OP.update(operator_auth.login(BASE))
    return OP


def _chaos_targets(case):
    """用例声明的故障注入目标（可多个）。"""
    targets = []
    if case.get("chaos"):
        targets.append("spectrum")
    if case.get("chaos_missing"):
        targets.extend(("trend", "charfreq"))
    return targets


#: 否定标记：真实结论常写成"BPFI/BSF/FTF 区无主导峰"来**排除**某个频率，
#: 直接数关键词会把外圈故障误判成"同时提到 BPFO 和 BPFI"（混合）——必须看前后文。
_NEGATIONS_BEFORE = ("无", "未", "排除", "不是", "非", "没有", "不见")
#: 后置否定：真实表述把否定放在词后（"BPFI…区无主导峰"），只用前缀窗口会漏
_NEGATIONS_AFTER = ("无主导", "无显著", "无峰", "无特征", "未见", "排除", "没有")
#: 分句符（不含 "/"：真实写法用 BPFI/BSF/FTF 这种斜杠并列，截断会丢掉后置否定）
_CLAUSE_SEP = "，。；,;、"


def _tail_after_sep(seg):
    """取最后一个分句符之后的部分——否定词属于哪个分句必须分清。"""
    cut = max((seg.rfind(s) for s in _CLAUSE_SEP), default=-1)
    return seg[cut + 1:] if cut >= 0 else seg


def _head_until_sep(seg):
    cut = min((i for i in (seg.find(s) for s in _CLAUSE_SEP) if i >= 0), default=-1)
    return seg[:cut] if cut >= 0 else seg


#: 中文机理名 ↔ 频率缩写：智能体常用中文表述，只认英文缩写会把它判成"没提及"。
#: 实测踩到：`包络谱在BPFO/BPFI/BSF/FTF±3Hz内无显著峰排除轴承缺陷` 与
#: `外圈缺陷(BPFO为主，兼内圈次峰)` 都因为只认 BPFO/BPFI 字面而被误判。
_CN_SYNONYM = {"BPFO": "外圈", "BPFI": "内圈"}


def _mentions(text, token, window=24):
    """token 是否被**正面**提及：**同一分句内**前后 window 字符出现否定即视为排除。

    前置例："未见 BPFI 特征峰"；后置例："BPFI/BSF/FTF 区无主导峰"（真实结论写法）。
    分句截断不可少："排除 BPFO，内圈 BPFI 命中" 里的"排除"属于前一分句，不能算到 BPFI 头上。
    window=24 而非 14：斜杠并列会把否定词推远（"BPFO/BPFI/BSF/FTF±3Hz内无显著峰"里
    "无显著"距 BPFO 有 18 字符），窗口太小会漏掉否定、把"排除某频率"读成"提及该频率"。"""
    up = text.upper()
    start = 0
    while True:
        idx = up.find(token, start)
        if idx < 0:
            return _mention_cn(text, token, window)
        before = _tail_after_sep(text[max(0, idx - window):idx])
        after = _head_until_sep(text[idx + len(token):idx + len(token) + window])
        if not (any(n in before for n in _NEGATIONS_BEFORE)
                or any(n in after for n in _NEGATIONS_AFTER)):
            return True
        start = idx + len(token)


def _mention_cn(text, token, window):
    """英文缩写没出现时退回中文机理名（外圈=BPFO、内圈=BPFI）。"""
    cn = _CN_SYNONYM.get(token)
    if not cn or cn not in text:
        return False
    idx = text.find(cn)
    while idx >= 0:
        before = _tail_after_sep(text[max(0, idx - window):idx])
        after = _head_until_sep(text[idx + len(cn):idx + len(cn) + window])
        if not (any(n in before for n in _NEGATIONS_BEFORE)
                or any(n in after for n in _NEGATIONS_AFTER)):
            return True
        idx = text.find(cn, idx + len(cn))
    return False


#: 主动拒绝下结论的措辞（4.2：未知故障应"无法识别时主动拒绝"，而不是硬给一个结论）
_REFUSALS = ("无法确定", "无法判断", "不能确定", "无法识别", "未能确认", "证据不足",
             "无法给出", "拒绝下结论", "待复测确认", "建议人工确认")


def is_hedged(text):
    """结论是否带保留（倾向性/待复测/证据不足…）：4.2 要求比较"证据质量"，
    仅靠 said==truth 会把"倾向性判断"和"确证结论"记成同一档（真实数据 #21/#27 就是这种）。"""
    t = text or ""
    return any(k in t for k in _REFUSALS + ("倾向", "待确认", "可能", "疑似"))


def classify_text(text):
    """从结论文本提取 said 标签。

    4.2 扩展：识别**混合故障**（BPFO 与 BPFI 都被正面提及）、**非轴承机理**
    （不平衡/松动——真值就是它们，不是"非轴承"）与**主动拒绝**（无法确定/证据不足）。
    否定表述（"BPFI 区无主导峰"）不计为提及。
    """
    t = text or ""
    bpfo, bpfi = _mentions(t, "BPFO"), _mentions(t, "BPFI")
    if bpfo and bpfi:
        return "bpfo_bpfi"
    if bpfo:
        return "bpfo"
    if bpfi:
        return "bpfi"
    # 非轴承机理：合成器为它们各自造了可区分的谱特征（见 tests/test_fault_physics.py），
    # 此前会落进 unknown 未评分桶——正确识别也拿不到分，等于把盲区算成"无法评分"。
    if _mentions(t, "不平衡") or "1X 占优" in t or "1X占优" in t:
        return "unbalance"
    if _mentions(t, "松动"):
        return "looseness"
    if any(k in t for k in _REFUSALS):
        return "undetermined"
    if "正常" in t or "无误报" in t or "未发现" in t:
        return "normal"
    if "缺陷" in t or "故障" in t or "越限" in t:
        return "abnormal-untyped"
    return "unknown"


def _wait_idle(timeout_s=90):
    """用例前置：等看门狗归位（上一轮 done 需走完复测关单；failed 需一轮恢复采样清态）。

    P0 问题3：采样由后端独立循环驱动，这里的 /api/device 只是观察（不再靠它推进采样），
    因此超时要按采样周期 × 判据样本数留量（RETEST_REQUIRED=3 个样本）。"""
    api("/api/scenario/set?key=fault.type&value=none", OP)
    api("/api/scenario/set?key=fault.severity&value=0", OP)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        api("/api/device?run_id=blind-prep", OP)
        if api("/api/autodiag/status", OP)["state"] == "idle":
            time.sleep(2)
            return True
        time.sleep(4)
    return False


def run_e2e_case(case):
    """故障用例：前置归位 → 注入 → 自动诊断 → 收集结论/延迟/工单。"""
    if not _wait_idle():
        return {"case": case["id"], "kind": "e2e", "truth": case["truth"],
                "said": "no-report", "correct": False, "triggered": False,
                "final_state": "not-idle", "failure_reason": "harness_wait_idle_timeout",
                "latency_s": None, "report_id": None, "task_created": None,
                "confidence": None, "conclusion": "", "evidence_index": None}
    scenario(fault=case["fault"], severity=case.get("severity", 0.8),
             rpm=case.get("rpm", 2980), noise=case.get("noise", 0.05),
             override=case.get("override"))
    targets = _chaos_targets(case)
    for target in targets:
        chaos(True, target)
    t0 = time.time()
    triggered, status = False, {}
    deadline = t0 + TRIGGER_TIMEOUT
    while time.time() < deadline:
        api("/api/device?run_id=blind", OP)   # 只读观察（采样由后端循环推进，P0 问题3）
        status = api("/api/autodiag/status", OP)
        if status["state"] in ("done", "failed", "partial", "verification_failed"):
            triggered = True
            break
        time.sleep(4)
    latency = round(time.time() - t0, 1)
    for target in targets:
        chaos(False, target)
    rep = None
    if status.get("report_id"):
        rows = api("/api/report/list?equip=P-101")["rows"]
        rep = next((r for r in rows if r["id"] == status["report_id"]), None)
    task_created = bool(status.get("task_id"))
    said = classify_text(rep["conclusion"]) if rep else "no-report"
    return {
        "case": case["id"], "kind": "e2e", "truth": case["truth"],
        "said": said, "correct": said == case["truth"],
        "triggered": triggered, "final_state": status.get("state"),
        # 用例有效性：全程没进入任何终态 = 报警根本没触发，这例**什么都没测**。
        # 不能让它静默地算成一个"答错"（2026-10-13 首轮实测：looseness 用例 severity 只到
        # notice 档，而自动唤起只由 severe 触发 → state 一直 idle）。
        "case_invalid": not triggered,
        "failure_reason": status.get("failure_reason"),
        "latency_s": latency if triggered else None,
        "report_id": status.get("report_id"), "task_created": task_created,
        "confidence": (rep or {}).get("confidence"),
        "conclusion": (rep or {}).get("conclusion", "")[:200],
        "hedged": is_hedged((rep or {}).get("conclusion")),
        # P0 问题2：闭环率必须看核验结果，而不是"有没有报告"
        "verification_ok": bool((status.get("verification") or {}).get("ok")),
        "evidence_index": status.get("evidence_index"),
    }


def run_normal_case(case):
    """正常/噪声用例：观察窗内管线不得触发（系统级误报）+ 直接咨询智能体（个体级误报）。"""
    scenario(noise=case.get("noise", 0.05))
    t0, fp_trigger = time.time(), False
    while time.time() - t0 < NORMAL_WINDOW_S:
        api("/api/device?run_id=blind", OP)
        st = api("/api/autodiag/status", OP)
        if st["state"] in ("dispatching", "running", "done"):
            fp_trigger = True
            break
        time.sleep(4)
    consult_text = ""
    try:
        consult_text = run_prompt(CONSULT_PROMPT, timeout=180)
    except Exception as exc:  # noqa: BLE001 —— 咨询失败如实记录，不算误报也不算通过
        consult_text = f"<consult_failed: {type(exc).__name__}>"
    said = classify_text(consult_text)
    return {
        "case": case["id"], "kind": "normal", "truth": "normal",
        "said": said, "correct": said == "normal",
        "pipeline_false_trigger": fp_trigger,
        "latency_s": None, "report_id": None, "task_created": None,
        "conclusion": consult_text[:200],
        "evidence_index": None,
    }


CASES = [
    # --- 正常 / 噪声（抗误报）：4.2 要求更多噪声强度与工况组合 ---
    {"id": "normal-baseline", "truth": "normal", "mode": "normal"},
    {"id": "noise-mild", "truth": "normal", "mode": "normal", "noise": 0.2},
    {"id": "noise-interference", "truth": "normal", "mode": "normal", "noise": 0.35},
    {"id": "noise-heavy", "truth": "normal", "mode": "normal", "noise": 0.5},
    {"id": "normal-lowrpm", "truth": "normal", "mode": "normal", "rpm": 1200},
    # --- 单故障 × 严重程度梯度 ---
    {"id": "bpfo-standard", "truth": "bpfo", "mode": "e2e", "fault": "bpfo", "severity": 0.8},
    {"id": "bpfi-standard", "truth": "bpfi", "mode": "e2e", "fault": "bpfi", "severity": 0.8},
    {"id": "bpfo-sev-low", "truth": "bpfo", "mode": "e2e", "fault": "bpfo", "severity": 0.6},
    {"id": "bpfo-boundary-kurt", "truth": "bpfo", "mode": "e2e", "fault": "bpfo", "severity": 0.45},
    {"id": "bpfo-boundary-rms", "truth": "bpfo", "mode": "e2e", "fault": "bpfo", "severity": 0.88},
    # --- 变转速（4.2：更多转速）---
    {"id": "bpfi-highrpm", "truth": "bpfi", "mode": "e2e", "fault": "bpfi", "severity": 0.8, "rpm": 2400},
    {"id": "bpfo-lowrpm", "truth": "bpfo", "mode": "e2e", "fault": "bpfo", "severity": 0.8, "rpm": 1200},
    {"id": "bpfo-highrpm", "truth": "bpfo", "mode": "e2e", "fault": "bpfo", "severity": 0.8, "rpm": 3600},
    # --- 混合故障（4.2：外圈+内圈，"未见完整验证"）---
    {"id": "mixed-bpfo-bpfi", "truth": "bpfo_bpfi", "mode": "e2e",
     "fault": "bpfo_bpfi", "severity": 1.0},
    # --- 非轴承机理（4.2）：真值就是机理本身——它们各自有可区分的谱特征，
    #     正确识别应得分，误判成轴承缺陷应扣分。
    #     looseness 需要 override：松动 @severity 1.0 的 RMS 只到 notice 档（≈6.4 < 7.1），
    #     而自动唤起只由 severe 触发，不 override 的话这例根本不进诊断链路（首轮实测空跑）。
    #     override 只改判定输入、不改波形，因此频谱仍是松动特征；峭度故意留在正常档，
    #     保留"松动不产生冲击成分"这一机理特征。 ---
    {"id": "unbalance-1x", "truth": "unbalance", "mode": "e2e", "fault": "unbalance", "severity": 1.0},
    {"id": "looseness-harmonics", "truth": "looseness", "mode": "e2e",
     "fault": "looseness", "severity": 1.0, "override": {"rms": 8.2, "kurtosis": 3.2}},
    # --- 未知故障（4.2：无法识别时应主动拒绝下结论）---
    {"id": "unknown-broadband", "truth": "undetermined", "mode": "e2e",
     "fault": "unknown", "severity": 1.0},
    # --- 工具故障（4.2：超时、部分数据缺失、模型限流）---
    {"id": "bpfo-tool-failure", "truth": "bpfo", "mode": "e2e", "fault": "bpfo", "severity": 0.8,
     "chaos": True},
    {"id": "bpfo-partial-data", "truth": "bpfo", "mode": "e2e", "fault": "bpfo", "severity": 0.8,
     "chaos_missing": True},
]


# ---- 规则算法基线（传统规则对照）：包络谱 ±3Hz 峰/底噪比 ----
# 判据用**峰/底噪比**而不是绝对幅值：宽带能量（强噪声、未建模故障）会把底噪整体抬高，
# 绝对幅值阈值会把它误判成"出现了特征峰"（2026-10-13 实测）。
RULE_RATIO_SIGNIFICANT = 20.0
RULE_RATIO_FLOOR = 12.0


def rule_baseline(case):
    """传统规则基线：包络谱在 BPFO/BPFI ±3Hz 的峰/底噪比超阈值即判该故障。

    4.2 扩展：能表达**混合**（两个频率都显著）、**非轴承/未知**（都不显著 → 正常）。
    该基线在扩展后更强，与智能体的对比更诚实（弱基线对比没有说服力）。"""
    rpm = case.get("rpm", 2980)
    x = synthesize(FS, N, rpm=rpm, fault_type=case.get("fault", "none"),
                   severity=case.get("severity", 0.0), noise=case.get("noise", 0.05), seed=42)
    freqs, amps = envelope_spectrum(x, FS)
    cf = characteristic_frequencies(rpm, 8, 0.32, 0)
    floor = float(np.median([a for f, a in zip(freqs, amps) if 10 < f < 1000])) or 1e-12
    ratios = {}
    for key in ("BPFO", "BPFI"):
        target = cf[key]
        peak = max((float(amps[i]) for i in range(len(freqs))
                    if abs(freqs[i] - target) <= 3.0), default=0.0)
        ratios[key] = peak / floor
    significant = [k for k, v in ratios.items() if v >= RULE_RATIO_SIGNIFICANT]
    if not significant:
        return "normal", ratios
    if len(significant) == 2:
        return "bpfo_bpfi", ratios
    return significant[0].lower(), ratios


def confidence_calibration(results):
    """按模型提交的置信度分组统计实测正确率（P1-3：置信度未做统计校准，
    此表只汇总实测；n<5 的分桶必须在报告里标注样本不足）。读取故障用例的报告置信度。"""
    buckets = {}
    for r in results:
        conf = r.get("confidence")
        if conf is None:
            continue
        key = round(float(conf), 2)
        b = buckets.setdefault(key, {"n": 0, "correct": 0})
        b["n"] += 1
        b["correct"] += 1 if r["correct"] else 0
    return [{"confidence": k, "n": v["n"], "empirical_correct_rate":
             round(v["correct"] / v["n"], 3),
             "note": "样本不足，仅实测汇总" if v["n"] < 5 else None}
            for k, v in sorted(buckets.items())]


def summarize(results):
    """指标汇总（审查 P0 问题2：此前三处口径会高估真实效果，已修正）。

    1. 准确率分母：此前排除 `no-report`/`unknown`，无结果或无法评分的样例**从总体指标里消失了**。
       现同时给出 `accuracy_all_samples`（全样本，失败/无结果一律计错）与 `accuracy_scored`
       （可评分样本），并显式给出 `unscored_rate` / `refusal_rate`。
    2. 任务完成率：此前只看有没有 `report_id`。现 `full_closure_rate` 要求
       `final_state=done` 且报告、工单、核验全部成立；旧口径保留为 `report_only_rate` 以便对照。
    3. 误报率分母：此前用全部用例数。现只用**阴性样本**（truth=normal），规则基线同样处理。
    """
    n_all = len(results)
    scored = [r for r in results if r["said"] not in ("no-report", "unknown", "harness-error")]
    matrix = {}
    for r in scored:
        matrix.setdefault(r["truth"], {}).setdefault(r["said"], 0)
        matrix[r["truth"]][r["said"]] += 1

    correct_all = sum(1 for r in results if r["correct"])
    correct_scored = sum(1 for r in scored if r["correct"])
    #: 阴性样本 = 真值 normal（误报率的分母只能是它们）
    negatives = [r for r in results if r["truth"] == "normal"]
    fp = sum(1 for r in negatives if r["said"] != "normal")

    e2e = [r for r in results if r["kind"] == "e2e"]
    report_only = [r for r in e2e if r.get("report_id")]
    closed = [r for r in e2e if (r.get("final_state") == "done" and r.get("report_id")
                                 and r.get("task_created") and r.get("verification_ok"))]
    # 审查第七节场景 C：工具失效时**不产生虚假运维结论**才是正确行为，不该按"是否说出真值"
    # 计分（智能体此时可能合理地拒绝给结论）。单独给一档：正确进入失败/部分完成态且
    # 没有落报告、没有建单 = 安全。
    case_by_id = {c["id"]: c for c in CASES}
    fault_cases = [r for r in e2e
                   if case_by_id.get(r.get("case"), {}).get("chaos")
                   or case_by_id.get(r.get("case"), {}).get("chaos_missing")]
    safe = [r for r in fault_cases
            if r.get("final_state") in ("failed", "partial", "verification_failed")
            and not r.get("report_id") and not r.get("task_created")]
    false_conclusion = [r for r in fault_cases
                        if r.get("final_state") == "done" and not r.get("verification_ok")]
    lats = sorted(r["latency_s"] for r in report_only if r.get("latency_s"))
    latency = {
        "n": len(lats),
        "min": min(lats) if lats else None,
        "median": round(statistics.median(lats), 1) if lats else None,
        "max": max(lats) if lats else None,
    }
    # 规则基线同一批用例（基线总能给出标签，故无"未评分"）
    rule_results = []
    for case in CASES:
        said, bands = rule_baseline(case)
        rule_results.append({"case": case["id"], "truth": case["truth"], "said": said,
                             "correct": said == case["truth"], "in_band_amp": bands})
    rule_correct = sum(1 for r in rule_results if r["correct"])
    rule_neg = [r for r in rule_results if r["truth"] == "normal"]
    rule_fp = sum(1 for r in rule_neg if r["said"] != "normal")
    # 基线也必须用**同一批有效用例**算"仅有效用例"口径：基线每次都给出标签、不存在"未触发"，
    # 若不剔除同一批无效用例，两个数字就不可比（等于拿苹果比橘子）。
    invalid_ids = {r["case"] for r in results if r.get("case_invalid")}
    rule_valid = [r for r in rule_results if r["case"] not in invalid_ids]
    return {
        "n_cases": n_all,
        "n_scored": len(scored),
        # 用例有效性：报了警但全程没进终态的用例（报警未触发/链路未启动）单独计数。
        # 它们**仍计入 accuracy_all_samples 的分母**（不让失败样例消失），同时另给一列
        # `accuracy_all_valid_cases` 供"只看有效用例"的读者对照。
        "invalid_case_n": sum(1 for r in results if r.get("case_invalid")),
        "accuracy_all_valid_cases": (round(
            sum(1 for r in results if r.get("correct") and not r.get("case_invalid"))
            / max(1, sum(1 for r in results if not r.get("case_invalid"))), 3) if n_all else None),
        "unscored_rate": round(1 - len(scored) / n_all, 3) if n_all else None,
        "accuracy_all_samples": round(correct_all / n_all, 3) if n_all else None,
        "accuracy_scored": round(correct_scored / len(scored), 3) if scored else None,
        "refusal_rate": round(sum(1 for r in results if r["said"] == "undetermined") / n_all, 3)
                         if n_all else None,
        "confusion_matrix": matrix,
        "false_positive_rate": round(fp / len(negatives), 3) if negatives else None,
        "negative_samples": len(negatives),
        "full_closure_rate": round(len(closed) / len(e2e), 3) if e2e else None,
        "report_only_rate": round(len(report_only) / len(e2e), 3) if e2e else None,
        "tool_failure_n": len(fault_cases),
        "tool_failure_safe_rate": round(len(safe) / len(fault_cases), 3) if fault_cases else None,
        "tool_failure_false_conclusion_n": len(false_conclusion),
        "latency_distribution_s": latency,
        "confidence_calibration": confidence_calibration(results),
        "rule_baseline": {"accuracy_all_samples": round(rule_correct / len(rule_results), 3)
                                              if rule_results else None,
                          "accuracy_all_valid_cases": (round(
                              sum(1 for r in rule_valid if r["correct"]) / len(rule_valid), 3)
                              if rule_valid and invalid_ids else None),
                          "false_positive_rate": round(rule_fp / len(rule_neg), 3) if rule_neg else None,
                          "note": f"包络谱±3Hz 峰/底噪比，显著阈 {RULE_RATIO_SIGNIFICANT}"
                                  f"（固定启发式，非调参；支持混合故障判定）",
                          "per_case": rule_results},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "evidence" / (
        "blind-benchmark-" + datetime.now().strftime("%Y%m%d-%H%M") + ".json")))
    ap.add_argument("--only", default=None, help="逗号分隔用例 id，调试单用例")
    args = ap.parse_args()

    login()   # P1 问题4：控制面写接口需登录签发的会话凭据

    cases = CASES
    if args.only:
        ids = set(args.only.split(","))
        cases = [c for c in CASES if c["id"] in ids]

    def _write(partial=False):
        """落盘（含增量）：每完成一例就写一次，中途被中止也不会丢掉已跑的结果。
        `partial=True` 表示还有用例没跑，指标只是阶段性结果。"""
        m = summarize(results)
        Path(args.out).write_text(json.dumps(
            {"generated_at": datetime.now().isoformat(timespec="seconds"),
             "n_target_cases": len(CASES), "completed_cases": len(results),
             "partial": partial, "metrics": m, "cases": results},
            ensure_ascii=False, indent=1), encoding="utf-8")
        return m

    results = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['id']} ...", flush=True)
        try:
            r = run_normal_case(case) if case["mode"] == "normal" else run_e2e_case(case)
        except Exception as exc:  # noqa: BLE001 —— 用例失败如实入册
            r = {"case": case["id"], "kind": case["mode"], "truth": case["truth"],
                 "said": "harness-error", "correct": False, "error": str(exc)[:200]}
        results.append(r)
        print("   ", json.dumps({k: r.get(k) for k in ("said", "correct", "latency_s",
                                                       "task_created")}, ensure_ascii=False), flush=True)
        _write(partial=True)   # 增量落盘：被中止也能保住已完成的用例
        # 用例间复位：fault none，等自动诊断归位（复测关单完成后 state 回 idle）
        api("/api/scenario/set?key=fault.type&value=none", OP)
        api("/api/scenario/set?key=fault.severity&value=0", OP)
        for _ in range(12):
            api("/api/device?run_id=blind", OP)
            if api("/api/autodiag/status", OP)["state"] == "idle":
                break
            time.sleep(5)
        time.sleep(3)

    m = _write()
    print("已写出", args.out)
    print(json.dumps({k: m[k] for k in ("accuracy_all_samples", "accuracy_scored",
                                        "full_closure_rate", "false_positive_rate",
                                        "refusal_rate", "latency_distribution_s")},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
