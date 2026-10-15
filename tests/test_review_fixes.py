"""审查第三节 6 项整改的回归（P0-1/P0-3/P1-5/P1-6；P0-2 在 test_blind_benchmark，P1-4 在 test_agh_policy）。

这些都是"加了门禁≠证明了"的地方：每条都验证**行为**，而不只是代码里出现了某个判断。
"""
import time

from conftest import VALID_EVIDENCE, api, fake_index, operator_headers
from test_autodiag import _fault, _make, ALARM, ad_db  # noqa: F401
from phmlite.autodiag import TERMINAL
from phmlite.db import DB
from phmlite.server import app

FAULT_NONE = {"fault": {"type": "none", "severity": 0.0}}


# ---------------- P0 问题1：报警与频谱必须同源 ----------------

def test_spectrum_and_waveform_share_the_sampled_window(base_url):
    """`/api/device` 的指标、`/api/waveform`、`/api/spectrum` 必须来自**同一段波形**。

    此前 spectrum/waveform 用固定种子重新合成，与报警用的 `POINT_SEED*1000003+seq` 不是
    同一段数据；现在三者带同一个 sample_seq 与一致的信号摘要。"""
    dev = api(base_url, "/api/device")
    seq = dev["sample_seq"]
    assert seq is not None, "采样循环应已产出快照"
    de = next(p for p in dev["points"] if p["name"] == "DE")

    spec = api(base_url, "/api/spectrum?point=DE&type=envelope&top=5")
    wave = api(base_url, "/api/waveform?point=DE&n=256")
    assert spec["source"] == "sampled" and wave["source"] == "sampled"
    assert spec["sample_seq"] == seq and wave["sample_seq"] == seq
    # 信号摘要必须与 /api/device 同测点的摘要一致——这是"同一段波形"的可核查证据
    assert spec["signal_rms"] == de["signal"]["signal_rms"]
    assert spec["signal_kurtosis"] == de["signal"]["signal_kurtosis"]
    assert wave["signal_rms"] == de["signal"]["signal_rms"]


def test_the_old_fixed_seed_realization_really_differs(base_url):
    """让上面这个回归**有意义**：固定种子（旧实现）合成的波形必须与采样波形不同。

    比**波形样本本身**而不是四舍五入后的标量：无故障注入时 rms 由确定性正弦主导，
    两个不同的噪声实现可能四舍五入到同一个值——用标量比较会随场景状态假失败/假通过
    （2026-10-13 实测：场景为 none 时该断言变成"两者相等"而误报）。"""
    from phmlite.dsp import synthesize
    from phmlite.server import FS, N

    wave = api(base_url, "/api/waveform?point=DE&n=64")
    assert wave["source"] == "sampled"
    fixed = synthesize(FS, N, rpm=2980, fault_type="none", severity=0.0, noise=0.05, seed=42)
    step = max(1, N // 64)
    old = [round(float(x), 4) for x in fixed[::step][:64]]
    assert wave["values"] != old, "固定种子与采样波形相同，说明同源改动没有实际生效"


def test_waveform_metrics_match_the_returned_samples(base_url):
    """波形摘要不是抄来的标签：用 wave 自己返回的点重算，应与之量级一致。

    采样是等间隔抽样（step>1），故只校验量级（±30%），但足以排除"摘要与波形无关"。"""
    import numpy as np
    wave = api(base_url, "/api/waveform?point=NDE&n=4096")
    x = np.asarray(wave["values"], dtype=float)
    assert abs(float(np.sqrt((x ** 2).mean())) - wave["signal_rms"]) / wave["signal_rms"] < 0.3


# ---------------- P0 问题3：复测必须看实测等级 ----------------

def _complete(ad_db, run_id):
    """runner：落库报告 + 关联工单（其余核验项由 fake_index 提供）。"""
    ad_db.submit_report(run_id, "P-101", "外圈BPFO缺陷", 0.9, VALID_EVIDENCE, diagnosis_status="confirmed", diagnosis_code="bpfo")
    ad_db.create_task("停机检查", "依据", "severe", "agent", run_id=run_id)


def _run(ad_db, monkeypatch, **kw):
    ad = _make(ad_db, lambda p, r: _complete(ad_db, r), monkeypatch, **kw)
    ad.observe(_fault(), [dict(ALARM)])
    deadline = time.time() + 5.0
    while time.time() < deadline and ad.state not in TERMINAL:
        time.sleep(0.05)
    return ad


def test_retest_blocks_close_when_measured_level_still_severe(ad_db, monkeypatch):
    """审查 P0 问题3：单次 severe 越限若还没达到连续报警条件，`severe_alarms` 是空的，
    但**当前样本已经严重异常**——复测不能把它当成"已恢复"并关单。"""
    ad = _run(ad_db, monkeypatch, index_provider=lambda rid: fake_index(rid))
    assert ad.state == "done", ad.state
    task_id = ad.task_id
    assert ad_db.get_task(task_id)["status"] == "open"

    # 关键：severe_alarms 为空（还没落报警行），但实测 level 已是 severe
    for _ in range(5):
        ad.observe(FAULT_NONE, [], measured=[{"name": "DE", "level": "severe"}])
    assert ad_db.get_task(task_id)["status"] == "open", "实测仍严重时不许关单"


def test_retest_closes_only_after_measured_normal(ad_db, monkeypatch):
    """反向：实测恢复正常（且连续样本 + 时间窗，测试里窗口置 0）才允许自动关单。"""
    ad = _run(ad_db, monkeypatch, index_provider=lambda rid: fake_index(rid))
    assert ad.state == "done"
    task_id = ad.task_id
    for _ in range(5):
        ad.observe(FAULT_NONE, [], measured=[{"name": "DE", "level": "normal"}])
    row = ad_db.get_task(task_id)
    assert row["status"] == "closed" and row["close_kind"] == "sim_recovery"


def test_retest_falls_back_to_alarm_rows_without_measured(ad_db, monkeypatch):
    """历史调用方/单测不传 measured 时退回旧口径（只看报警行），保证既有行为无缝。"""
    ad = _run(ad_db, monkeypatch, index_provider=lambda rid: fake_index(rid))
    assert ad.state == "done"
    task_id = ad.task_id
    ad.observe(FAULT_NONE, [{"level": "severe"}])
    assert ad_db.get_task(task_id)["status"] == "open"


# ---------------- P1 问题5：工具成功 ≠ 业务成功 ----------------

def test_write_call_error_blocks_done(ad_db, monkeypatch):
    """审查 P1 问题5：写接口的工具调用若是失败结果，不得记 done。

    此前 `curl -s` 在 HTTP 503 时仍返回 0，工具层 isError=false 会被当成业务成功。"""
    ad = _run(ad_db, monkeypatch,
              index_provider=lambda rid: fake_index(rid, write_error=True))
    assert ad.state != "done", "写接口工具调用失败却记了完成"
    assert "write_call_unverified" in (ad.verification or {}).get("problems", [])
    assert (ad.verification or {}).get("write_calls_ok") is False


def test_write_ok_if_each_endpoint_succeeded_once(monkeypatch):
    """重试成功后不算失败：智能体第一次写被 400 拒、按提示修正后重试成功是**正常行为**，
    把失败的那次也算进来会把成功的运行判成未核验（2026-10-13 真机实测踩到：
    报告与工单都落库了，却因为第一次 submit 被拒而报 write_call_unverified）。"""
    from phmlite.agh_evidence import build_index

    def ev(seq, typ, data):
        return {"session_key": "s", "seq": seq, "ts": "2026-10-13T00:00:00Z", "type": typ,
                "data": data}

    events = [
        {"session_key": "s", "seq": 1, "ts": "2026-10-13T00:00:00Z", "type": "turn/start",
         "data": '{"turn": 1, "prompt": "诊断 run_id=auto-x"}  '},
        ev(2, "tool/call", '{"toolUseId":"a","name":"shell","args":{"command":'
                           '"curl.exe -sS -f \\"http://127.0.0.1:8181/api/report/submit\\""}}'),
        ev(3, "tool/result", '{"toolUseId":"a","isError":true}'),
        ev(4, "tool/call", '{"toolUseId":"b","name":"shell","args":{"command":'
                           '"curl.exe -sS -f -G \\"http://127.0.0.1:8181/api/report/submit\\""}}'),
        ev(5, "tool/result", '{"toolUseId":"b","isError":false}'),
        ev(6, "tool/call", '{"toolUseId":"c","name":"shell","args":{"command":'
                           '"curl.exe -sS -f -G \\"http://127.0.0.1:8181/api/task/create\\""}}'),
        ev(7, "tool/result", '{"toolUseId":"c","isError":false}'),
        *[ev(8 + i, "tool/call", '{"toolUseId":"f%d","name":"shell","args":{"command":'
                                 '"curl.exe -sS -f \\"http://127.0.0.1:8181/api/device\\""}}' % i)
          for i in range(3)],
        *[ev(20 + i, "tool/result", '{"toolUseId":"f%d","isError":false}' % i) for i in range(3)],
    ]
    # Successful write fixtures include actual request identity and JSON response.
    import json
    for event in events:
        data = json.loads(event["data"])
        if event["type"] == "tool/call" and data["toolUseId"] in ("b", "c"):
            data["args"]["command"] += ' --data-urlencode "run_id=auto-x"'
        if event["type"] == "tool/result" and data["toolUseId"] in ("b", "c"):
            data["content"] = [{"type": "text", "text": '{"id":1,"run_id":"auto-x"}\n[exit 0]'}]
        event["data"] = json.dumps(data)
    index = build_index(events, "auto-x")
    assert index["checks"]["tool_error_count"] == 1        # 失败的那次如实计数
    assert index["checks"]["write_calls_ok"] is True       # 但两个端点都成功过 → 通过


# ---------------- P1 问题6：报警行 ≠ 独立故障事件 ----------------

def test_repeated_breach_reuses_one_incident(tmp_path):
    """审查 P1 问题6：持续异常会周期性再落报警行，但它们属于**同一起**故障事件。

    此前 maybe_alarm 触发后把计数清零，持续异常每 3 个采样就再落一行——归档证据里
    1314 条报警因此被误读成 1314 起独立故障。"""
    db = DB(tmp_path / "inc.db").migrate()
    rows = []
    for seq in range(1, 13):   # 连续 12 个采样都严重越限
        a = db.maybe_alarm("P-101", "DE", 9.0, 12.0, 70.0, seq=seq)
        if a:
            rows.append(a)
    assert len(rows) == 4, [r["kind"] for r in rows]     # 每 3 个采样落一行
    assert rows[0]["kind"] == "new"
    assert all(r["kind"] == "repeat" for r in rows[1:])
    assert len({r["incident_id"] for r in rows}) == 1     # 同一起事件

    s = db.alarm_summary("P-101")
    assert s["alarm_rows"] == 4 and s["incidents"] == 1 and s["repeat_rows"] == 3

    # 恢复正常 → 事件结束；再次越限属于**新的**事件
    db.maybe_alarm("P-101", "DE", 2.0, 2.5, 5.0, seq=13)
    again = [db.maybe_alarm("P-101", "DE", 9.0, 12.0, 70.0, seq=s)
             for s in (14, 15, 16)]
    newest = [a for a in again if a][-1]
    assert newest["kind"] == "new"
    assert db.alarm_summary("P-101")["incidents"] == 2


def test_alarm_history_exposes_incident_layer(base_url):
    """对外接口必须能看出"报警行数 ≠ 故障事件数"，否则评审无法识别重复报警。"""
    d = api(base_url, "/api/alarm/history")
    assert "summary" in d and {"alarm_rows", "incidents", "repeat_rows"} <= set(d["summary"])
    assert d["summary"]["alarm_rows"] >= d["summary"]["incidents"]
    if d["rows"]:
        assert d["rows"][0]["kind"] in ("new", "repeat", "unclassified")


# ---------------- 可空场景键必须能清除（本轮实测踩到） ----------------

def test_nullable_scenario_keys_can_be_cleared(base_url):
    """`override.*` 是临时注入手段，必须能撤掉。

    HTTP 只能传字符串，而旧 `_coerce` 只认数字 → `override.rms=` 一律 400，
    **override 一旦设上就再也清不掉**：盲测用例复位、演示恢复现场都会卡住。
    （2026-10-13 实测：加了 override 之后基准第一例就 400。）"""
    op = operator_headers(base_url)
    api(base_url, "/api/scenario/set?key=fault.type&value=none", op)
    d = api(base_url, "/api/scenario/set?key=override.rms&value=8.2", op)
    assert d["override"]["rms"] == 8.2
    for empty in ("", "null", "none"):
        d = api(base_url, f"/api/scenario/set?key=override.rms&value={empty}", op)
        assert d["override"]["rms"] is None, empty


def test_non_nullable_keys_still_reject_empty(base_url):
    """放开可空键不能顺手把别的键也放空。"""
    import urllib.error
    op = operator_headers(base_url)
    for key in ("fault.severity", "rpm", "noise"):
        try:
            api(base_url, f"/api/scenario/set?key={key}&value=", op)
            raise AssertionError(f"{key} 不该接受空值")
        except urllib.error.HTTPError as e:
            assert e.code == 400, key


# ---------------- 证据形状：对象写法必须被接受（真机验收因此 FAIL 过） ----------------

def test_evidence_key_numbers_accepts_object_and_array():
    """P0：`key_numbers` 只接受数组时，智能体写成对象 `{"kurtosis":10.497}` 会被判"为空"、
    报告接口返回 400、**报告落不了库**（2026-10-13 真机验收 FAIL 的原因）。
    两种写法都是合法证据载体；提示词也补明了这一点。"""
    from phmlite.autodiag import parse_evidence_steps

    def ev(numbers):
        import json
        return json.dumps([{"step": "1", "tool": "/api/device",
                            "key_numbers": numbers, "supports": "时域异常"}], ensure_ascii=False)

    steps, problem = parse_evidence_steps(ev({"kurtosis": 10.497, "rms": 6.7}))
    assert problem is None and len(steps) == 1
    steps, problem = parse_evidence_steps(ev(["DE.kurtosis=10.50", "理论BPFO=135.09Hz"]))
    assert problem is None and len(steps) == 1
    # 真空值仍然拒绝，且错误信息要说明期望形状（智能体靠它自我修正）
    for empty in ({}, [], {"x": None}, ""):
        _, problem = parse_evidence_steps(ev(empty))
        assert problem and "key_numbers" in problem, empty
    _, problem = parse_evidence_steps(ev({}))
    assert "数组" in problem and "对象" in problem


def test_transient_agh_failure_is_retried(monkeypatch):
    """2026-10-13 实测：AGH 调用有约 1/10 的**偶发**内部错误（stderr 只有百来字节，
    同一命令立刻重跑就成功）。此前只有会话层重试一次、**provider 预检完全没有重试**，
    预检偶发一次失败就把整轮诊断判死（真机验收 ③ 间歇性 FAIL 的原因）。
    现在两类瞬时失败统一退避重试；非瞬时失败立即抛出，不浪费时间。"""
    from phmlite import agh_runtime as ar

    calls = []

    def flaky(arguments, *, timeout, command_first=False, workspace=None):
        calls.append(1)
        if len(calls) < 3:
            raise ar.AGHError("cli_failed", "AGH 调用失败（退出码 1）", detail="INTERNAL_ERROR")
        return '{"ok": true}'

    monkeypatch.setattr(ar, "_invoke", flaky)
    assert ar._invoke_retrying(["doctor"], timeout=1) == '{"ok": true}'
    assert len(calls) == 3

    # 非瞬时失败（例如配置错误）必须立刻抛出，且不重试
    calls.clear()

    def fatal(arguments, *, timeout, command_first=False, workspace=None):
        calls.append(1)
        raise ar.AGHError("runtime_missing", "运行时缺失")

    monkeypatch.setattr(ar, "_invoke", fatal)
    try:
        ar._invoke_retrying(["doctor"], timeout=1)
        raise AssertionError("应抛出")
    except ar.AGHError as exc:
        assert exc.category == "runtime_missing" and len(calls) == 1


def test_session_broken_detected_from_internal_stderr(monkeypatch):
    """会话级故障的判定必须看**进程内的原始 stderr**，不能只看消息。

    消息里的 stderr 默认被省略（只在调试开关下才有），只按消息判断会让自愈
    时灵时不灵——真机验收 ② 就是这么挂过一次。"""
    from phmlite import agh_runtime as ar

    hidden = ar.AGHError("cli_failed", "AGH 调用失败（退出码 1，stderr 650 字节）",
                         detail="agnes: INTERNAL_ERROR (-32603) RESULT_INVALID")
    assert ar._session_broken(hidden) is True
    other = ar.AGHError("cli_failed", "AGH 调用失败（退出码 1）", detail="E_PROVIDER_UNAUTHORIZED")
    assert ar._session_broken(other) is False


def test_report_submit_accepts_object_key_numbers(base_url):
    """接口层同一条判据：对象写法必须能落库。"""
    import json
    import urllib.parse
    op = operator_headers(base_url)
    run = api(base_url, "/api/autodiag/status", op).get("run_id") or "auto-review-probe"
    ev = json.dumps([{"step": "1", "tool": "/api/device",
                      "key_numbers": {"kurtosis": 10.497}, "supports": "时域异常"}],
                    ensure_ascii=False)
    q = urllib.parse.urlencode({"run_id": run, "equip": "P-101", "conclusion": "形状校验用例",
                                "confidence": 0.5, "evidence_json": ev})
    try:
        d = api(base_url, f"/api/report/submit?{q}", op)
        assert d.get("id")
    except Exception as exc:  # noqa: BLE001 —— 运行门禁拒绝（无活动 run）不是形状问题
        assert "400" not in str(exc) and "证据" not in str(exc), exc
