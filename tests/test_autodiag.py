"""自动诊断状态机：AGH 失败收尾、人工模式、去重与恢复。"""
import importlib
import time

import pytest

from conftest import VALID_EVIDENCE, api, fake_index
from phmlite.autodiag import TERMINAL, AutoDiag
from phmlite.agh_runtime import AGHError
from phmlite.server import app

ALARM = {"equip": "P-101", "point": "DE", "ts": "2026-10-03T10:00:00", "level": "severe",
         "kpi": "rms", "value": 8.87, "msg": "rms=8.87 连续3点超过severe阈值"}


def _operation_mode():
    try:
        return importlib.import_module("phmlite.operation_mode")
    except ImportError as exc:
        pytest.fail(f"persistent operation mode is missing: {exc}")


def _make(db, runner, monkeypatch, data_dir=None, provider_probe=None, index_provider=None):
    from phmlite import autodiag

    monkeypatch.setattr(autodiag, "probe_provider", provider_probe or (lambda: None), raising=False)
    # P0 问题2：核验门禁要读执行索引；离线单测注入同形假索引（真实索引依赖 AGH 会话库）
    ad = AutoDiag(db, data_dir or app.state.data_dir, runner=runner,
                  index_provider=index_provider or (lambda run_id: fake_index(run_id)))
    ad.enabled = True  # conftest 对全局实例关闭自动唤起；状态机单测强制开启
    ad.retry_backoff = 0.2  # 失败重试退避在单测里缩短，避免拖慢收敛超时
    monkeypatch.setattr(ad, "_health_check", lambda: None)
    return ad


def _fault(kind="bpfo", severity=0.8):
    return {"fault": {"type": kind, "severity": severity}}


def _settle(ad, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ad.state in TERMINAL:
            return ad.status()
        time.sleep(0.05)
    raise AssertionError(f"会话未在 {timeout}s 内收敛，state={ad.state}")


@pytest.fixture()
def ad_db(base_url):
    return app.state.db


def test_disabled_watchdog_is_noop(ad_db, monkeypatch):
    ad = _make(ad_db, runner=lambda *a, **k: pytest.fail("不应唤起"), monkeypatch=monkeypatch)
    ad.enabled = False
    ad.observe(_fault(), [dict(ALARM)])
    assert ad.status()["state"] == "idle"


def test_trigger_once_and_dedup(ad_db, monkeypatch):
    calls = []

    def complete(prompt, run_id):
        calls.append((prompt, run_id))
        ad_db.submit_report(run_id, "P-101", "BPFO 外圈缺陷", 0.92, VALID_EVIDENCE, diagnosis_status="confirmed", diagnosis_code="bpfo")
        ad_db.create_task("停机检查", "BPFO 外圈缺陷", "severe", "agent", run_id=run_id)

    ad = _make(ad_db, runner=complete, monkeypatch=monkeypatch)
    ad.observe(_fault(), [dict(ALARM)])
    status = _settle(ad)
    assert status["state"] == "done" and status["report_id"] >= 1
    assert "检测到驱动端" in status["events"][0]["text"]
    assert f"run_id={status['run_id']}" in calls[0][0]
    assert ad_db.get_task(status["task_id"])["run_id"] == status["run_id"]
    texts = [e["text"] for e in status["events"]]
    assert any("取证诊断" in t for t in texts) and any("诊断完成" in t for t in texts)
    n_reports = len([r for r in ad_db.report_list() if r["run_id"] == status["run_id"]])
    # 同一故障重复 severe 报警：不重复唤起
    ad.observe(_fault(), [dict(ALARM)])
    ad.observe(_fault(), [dict(ALARM)])
    time.sleep(0.3)
    assert len([r for r in ad_db.report_list() if r["run_id"].startswith("auto-")]) == n_reports


def test_recovery_resets_dedup(ad_db, monkeypatch):
    def complete(prompt, run_id):
        ad_db.submit_report(run_id, "P-101", "外圈BPFO缺陷", 0.9, VALID_EVIDENCE, diagnosis_status="confirmed", diagnosis_code="bpfo")
        ad_db.create_task("检查", "依据", "severe", "agent", run_id=run_id)

    ad = _make(ad_db, runner=complete, monkeypatch=monkeypatch)
    ad.observe(_fault(), [dict(ALARM)])
    _settle(ad)
    ad.observe({"fault": {"type": "none", "severity": 0.0}}, [])
    # 恢复后进入复测计数（不再立刻归位）：工单待关，同故障去重键已解除
    assert ad.status()["state"] == "done"
    calls = []

    def complete_again(prompt, run_id):
        calls.append(run_id)
        ad_db.submit_report(run_id, "P-101", "外圈BPFO缺陷", 0.9, VALID_EVIDENCE, diagnosis_status="confirmed", diagnosis_code="bpfo")
        ad_db.create_task("检查", "依据", "severe", "agent", run_id=run_id)

    ad._runner = complete_again
    ad.observe(_fault("bpfi"), [dict(ALARM)])   # 复测未完成时重新注入 → 中止复测并再次唤起
    status = _settle(ad)
    assert status["state"] == "done" and calls
    assert any("复测中止" in e["text"] for e in status["events"])


def test_manual_work_order_is_not_linked_to_agent_run(ad_db, monkeypatch):
    def runner(prompt, run_id):
        ad_db.submit_report(run_id, "P-101", "外圈BPFO缺陷", 0.9, VALID_EVIDENCE, diagnosis_status="confirmed", diagnosis_code="bpfo")
        ad_db.create_task("人工检查", "人工来源", "notice", "manual")

    ad = _make(ad_db, runner=runner, monkeypatch=monkeypatch)
    ad.observe(_fault(), [dict(ALARM)])
    status = _settle(ad)

    # P0 问题2：有报告但没有关联工单 → 只到 partial，不得记 done
    assert status["state"] == "partial"
    assert status["task_id"] is None
    assert status["failure_reason"] == "task_missing"
    assert status["verification"]["task_linked"] is False


def test_failed_agh_session_removes_partial_run_artifacts(ad_db, monkeypatch):
    before_reports = len(ad_db.report_list())
    before_tasks = len(ad_db.task_list())

    def partial_then_fail(prompt, run_id):
        ad_db.submit_report(run_id, "P-101", "partial", 0.9, VALID_EVIDENCE, diagnosis_status="confirmed", diagnosis_code="bpfo")
        ad_db.create_task("partial", "partial", "severe", "agent", run_id=run_id)
        raise AGHError("cli_failed", "model call failed after a tool action")

    ad = _make(ad_db, runner=partial_then_fail, monkeypatch=monkeypatch)
    ad.observe(_fault(), [dict(ALARM)])
    status = _settle(ad)

    assert status["state"] == "failed"
    assert not [r for r in ad_db.report_list() if r["run_id"] == status["run_id"]]
    assert not [t for t in ad_db.task_list() if t.get("run_id") == status["run_id"]]
    assert len(ad_db.report_list()) == before_reports
    assert len(ad_db.task_list()) == before_tasks


def test_agh_failure_finishes_failed_without_writing_report_or_task(ad_db, monkeypatch):
    before_reports = len(ad_db.report_list())
    before_tasks = len(ad_db.task_list())
    ad = _make(ad_db, runner=lambda prompt, run_id: (_ for _ in ()).throw(
        AGHError("runtime_unavailable", "AGH daemon 不在线")), monkeypatch=monkeypatch)
    ad.observe(_fault(severity=0.9), [dict(ALARM)])
    status = _settle(ad)
    assert status["state"] == "failed" and status["mode"] == "agh"
    assert status["failure_reason"] == "runtime_unavailable"
    assert status["report_id"] is None and status["task_id"] is None
    assert len(ad_db.report_list()) == before_reports and len(ad_db.task_list()) == before_tasks
    assert any("AGH" in e["text"] and "失败" in e["text"] for e in status["events"])
    assert not any("已自主启动取证诊断" in e["text"] for e in status["events"])


def test_manual_mode_skips_automatic_diagnosis(ad_db, monkeypatch, tmp_path):
    mode = _operation_mode()
    mode.set_mode(tmp_path, "manual")
    calls = []
    ad = _make(ad_db, runner=lambda *args: calls.append(args), monkeypatch=monkeypatch, data_dir=tmp_path)

    ad.observe(_fault(), [dict(ALARM)])

    assert ad.status()["state"] == "idle" and calls == []


def test_provider_probe_failure_prevents_diagnostic_runner_and_writes(ad_db, monkeypatch):
    before_reports = len(ad_db.report_list())
    before_tasks = len(ad_db.task_list())
    calls = []

    def failed_probe():
        raise AGHError("provider_unavailable", "fake invalid provider")

    ad = _make(ad_db, runner=lambda *args: calls.append(args), monkeypatch=monkeypatch,
               provider_probe=failed_probe)
    ad.observe(_fault(), [dict(ALARM)])
    status = _settle(ad)

    assert status["state"] == "failed" and status.get("failure_reason") == "provider_unavailable"
    assert calls == [] and len(ad_db.report_list()) == before_reports and len(ad_db.task_list()) == before_tasks


def test_status_endpoint_shape(base_url):
    d = api(base_url, "/api/autodiag/status")
    assert {"enabled", "state", "events"} <= set(d)
    assert isinstance(d["events"], list)


def test_failed_run_does_not_redispatch_same_fault(ad_db, monkeypatch):
    """F4 方案 a：failed 占去重键——持续越限时失败后同故障不再反复唤起；
    撤销注入（恢复归位清键）后重新注入才再次诊断。"""
    calls = []

    def failing(prompt, run_id):
        calls.append(run_id)
        raise AGHError("runtime_unavailable", "AGH daemon 不在线")

    ad = _make(ad_db, runner=failing, monkeypatch=monkeypatch)
    for _ in range(3):  # 持续越限：3 次严重报警（模拟连续采样）
        ad.observe(_fault(), [dict(ALARM)])
        _settle(ad)
    assert len(set(calls)) == 1 and ad.status()["state"] == "failed"  # 同一 run 只派发一轮（含重试）
    assert any("恢复正常" in e["text"] for e in status_events(ad))

    ad.observe({"fault": {"type": "none", "severity": 0.0}}, [])  # 撤销注入 → 归位清键
    assert ad.status()["state"] == "idle"
    ad.observe(_fault("bpfo"), [dict(ALARM)])                     # 重新注入 → 再次唤起
    _settle(ad)
    assert len(set(calls)) == 2                                   # 新 run_id，新一轮派发


def status_events(ad):
    return ad.status()["events"]
