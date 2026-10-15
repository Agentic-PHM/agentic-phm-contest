"""P0 问题3 工单生命周期：关单接口、自动复测关单、三态区分。"""
import time

from conftest import VALID_EVIDENCE, api, operator_headers
from test_autodiag import _make, ALARM, ad_db  # noqa: F401
from phmlite.autodiag import TERMINAL
from phmlite.server import app

FAULT_NONE = {"fault": {"type": "none", "severity": 0.0}}


def _settle(ad, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ad.state in TERMINAL:
            return
        time.sleep(0.05)
    raise AssertionError(f"会话未收敛: {ad.state}")


def _fault(kind="bpfo", severity=0.8):
    return {"fault": {"type": kind, "severity": severity}}


def test_close_task_sets_status_and_note(ad_db):
    tid = ad_db.create_task("检查", "依据", "severe", "manual")
    assert ad_db.get_task(tid)["status"] == "open"
    closed = ad_db.close_task(tid, note="复测通过")
    assert closed["status"] == "closed" and closed["close_note"] == "复测通过"
    assert closed["closed_ts"]
    assert ad_db.close_task(tid, note="再次关闭") is None  # 幂等：已关闭不可再关


def test_task_close_endpoint(ad_db, base_url):
    tid = ad_db.create_task("检查", "依据", "severe", "manual")
    op = operator_headers(base_url)   # P1 问题6：关单属控制面操作，需会话凭据
    d = api(base_url, f"/api/task/close?id={tid}&note=%E5%A4%84%E7%BD%AE%E5%AE%8C%E6%88%90", op)
    assert d["status"] == "closed" and "处置完成" in d["close_note"]
    r = api(base_url, "/api/task/list?status=closed")
    assert any(row["id"] == tid for row in r["rows"])
    import urllib.error
    try:
        api(base_url, f"/api/task/close?id={tid}", op)  # 已关闭再关 → 404
        raise AssertionError("应 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_auto_retest_closes_agent_task(ad_db, monkeypatch):
    """B 方案核心链路：诊断 done + 工单 open → 恢复后连续 3 次采样 → 自动关单并播报。"""
    def complete(prompt, run_id):
        ad_db.submit_report(run_id, "P-101", "外圈BPFO缺陷", 0.9, VALID_EVIDENCE, diagnosis_status="confirmed", diagnosis_code="bpfo")
        ad_db.create_task("停机检查", "依据", "severe", "agent", run_id=run_id)

    ad = _make(ad_db, complete, monkeypatch)
    ad.observe(_fault(), [dict(ALARM)])
    _settle(ad)
    task_id = ad.task_id
    assert task_id and ad_db.get_task(task_id)["status"] == "open"

    for _ in range(3):  # 恢复：连续 3 次采样无严重报警
        ad.observe(FAULT_NONE, [])
    closed = ad_db.get_task(task_id)
    assert closed["status"] == "closed" and "复测" in closed["close_note"]
    # P1 问题5：关单依据必须记成"仿真恢复验证"，且文案不得宣称维修完成
    assert closed["close_kind"] == "sim_recovery"
    assert "未验证实际维修" in closed["close_note"]
    assert "已修好" not in closed["close_note"] and "维修完成" not in closed["close_note"]
    events = [e["text"] for e in ad.status()["events"]]
    assert any("仿真恢复验证通过" in t and f"工单#{task_id}" in t for t in events)
    assert not any("处置闭环完成" in t for t in events)
    assert ad.status()["state"] == "idle"  # 闭环完成后归位
    phases = ad.status()["phases"]
    assert phases["diagnosis_done"] is True and phases["task_closed"] is True


def test_auto_retest_counts_only_consecutive_normal_polls(ad_db, monkeypatch):
    """复测中途故障重新注入 → 计数清零、工单保持 open、同一故障可再次唤起。"""
    def complete(prompt, run_id):
        ad_db.submit_report(run_id, "P-101", "外圈BPFO缺陷", 0.9, VALID_EVIDENCE, diagnosis_status="confirmed", diagnosis_code="bpfo")
        ad_db.create_task("停机检查", "依据", "severe", "agent", run_id=run_id)

    ad = _make(ad_db, complete, monkeypatch)
    ad.observe(_fault(), [dict(ALARM)])
    _settle(ad)
    task_id = ad.task_id

    ad.observe(FAULT_NONE, [])       # 复测计数 1
    ad.observe(FAULT_NONE, [])       # 复测计数 2
    ad.observe(_fault(), [dict(ALARM)])  # 中途重新注入 → 中止复测
    assert ad_db.get_task(task_id)["status"] == "open"
    assert any("复测中止" in e["text"] for e in ad.status()["events"])
    assert ad.status()["phases"]["task_closed"] is False


def test_manual_task_is_never_auto_closed(ad_db, monkeypatch):
    """自动关单只针对智能体工单；无关联工单的 run 复测期不误关别人的单。"""
    tid = ad_db.create_task("人工工单", "人工", "severe", "manual")

    def complete(prompt, run_id):
        ad_db.submit_report(run_id, "P-101", "外圈BPFO缺陷", 0.9, VALID_EVIDENCE, diagnosis_status="confirmed", diagnosis_code="bpfo")  # 有报告无工单

    ad = _make(ad_db, complete, monkeypatch)
    ad.observe(_fault(), [dict(ALARM)])
    _settle(ad)
    assert ad.task_id is None
    for _ in range(5):
        ad.observe(FAULT_NONE, [])
    assert ad_db.get_task(tid)["status"] == "open"  # 人工工单不受影响


# ---- P1 问题5：复测依据必须来自测量 + 时间窗，且不得把仿真恢复说成维修完成 ----

def _diagnosed_run(ad_db, monkeypatch):
    """跑一轮到 done 并留下 open 的智能体工单，返回 AutoDiag。"""
    def complete(prompt, run_id):
        ad_db.submit_report(run_id, "P-101", "外圈BPFO缺陷", 0.9, VALID_EVIDENCE, diagnosis_status="confirmed", diagnosis_code="bpfo")
        ad_db.create_task("停机检查", "依据", "severe", "agent", run_id=run_id)

    ad = _make(ad_db, complete, monkeypatch)
    ad.observe(_fault(), [dict(ALARM)])
    _settle(ad)
    assert ad.state == "done" and ad.task_id
    return ad


def test_retest_requires_measured_recovery_not_just_scenario_flag(ad_db, monkeypatch):
    """复测判据必须读测量：场景开关置 none 但仍有严重报警时，一次都不许计入。

    修复前 `_observe_recovery_locked` 收下 severe_alarms 却从不读它，
    于是"解除仿真注入"被直接当成"设备已恢复"。"""
    ad = _diagnosed_run(ad_db, monkeypatch)
    for _ in range(5):
        ad.observe(FAULT_NONE, [dict(ALARM)])   # 场景已复位，但测量仍严重越限
    assert ad_db.get_task(ad.task_id)["status"] == "open"
    assert ad.status()["retest"]["count"] == 0


def test_retest_requires_time_window(ad_db, monkeypatch):
    """样本数够了但时间窗未跨过 → 不得关单（只数样本的话，采样密时几秒就能"通过"）。"""
    ad = _diagnosed_run(ad_db, monkeypatch)
    ad.retest_window_s = 3600
    for _ in range(6):
        ad.observe(FAULT_NONE, [])
    assert ad_db.get_task(ad.task_id)["status"] == "open"
    assert ad.status()["retest"]["count"] == 6
    assert ad.status()["retest"]["pending"] is True


def test_retest_window_is_exposed_in_status(ad_db, monkeypatch):
    ad = _diagnosed_run(ad_db, monkeypatch)
    ad.retest_window_s = 42
    ad.observe(FAULT_NONE, [])
    retest = ad.status()["retest"]
    assert retest["window_s"] == 42 and retest["required"] == 3
    assert retest["elapsed_s"] >= 0


def test_manual_close_is_distinguishable_from_simulation_recovery(ad_db, base_url):
    """关单依据可区分：人工处置确认 vs 仿真恢复验证（P1 问题5）。"""
    op = operator_headers(base_url)
    tid = ad_db.create_task("检查", "依据", "severe", "manual")
    d = api(base_url, f"/api/task/close?id={tid}&note=%E7%BB%B4%E4%BF%AE%E5%B7%B2%E5%AE%8C%E6%88%90", op)
    assert d["status"] == "closed"
    assert ad_db.get_task(tid)["close_kind"] == "manual"

    tid2 = ad_db.create_task("检查2", "依据", "severe", "agent", run_id="r-x")
    closed = ad_db.close_task(tid2, note="仿真恢复验证通过", kind="sim_recovery")
    assert closed["close_kind"] == "sim_recovery"
