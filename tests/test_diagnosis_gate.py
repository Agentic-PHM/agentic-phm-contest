"""P0 问题2 回归：诊断"成功"的门槛——报告存在只是必要条件。

覆盖：证据结构校验（接口与状态机共用同一判据）、done 需报告+工单+证据+工具调用+关联，
达不到时降级 partial / verification_failed，以及执行索引落盘失败撤销成功状态。
"""
import json
import time
import urllib.error
from urllib.parse import quote

from conftest import VALID_EVIDENCE, api, fake_index
from test_api import _allow_active_run
from test_autodiag import ALARM, _fault, _make, ad_db  # noqa: F401
from phmlite.autodiag import TERMINAL, parse_evidence_steps

BAD_EVIDENCE = "{}"


def _settle(ad, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ad.state in TERMINAL:
            return ad.status()
        time.sleep(0.05)
    raise AssertionError(f"会话未收敛: {ad.state}")


def _run_with(ad_db, monkeypatch, *, evidence=VALID_EVIDENCE, with_task=True,
              index_provider=None):
    """跑一轮自动诊断：runner 落库报告（+可选工单），索引来源可注入。"""
    def runner(prompt, run_id):
        ad_db.submit_report(run_id, "P-101", "外圈BPFO缺陷", 0.9, evidence, diagnosis_status="confirmed", diagnosis_code="bpfo")
        if with_task:
            ad_db.create_task("停机检查", "依据", "severe", "agent", run_id=run_id)

    ad = _make(ad_db, runner=runner, monkeypatch=monkeypatch, index_provider=index_provider)
    ad.observe(_fault(), [dict(ALARM)])
    return _settle(ad)


# ---- 证据结构校验（接口与状态机共用）----

def test_parse_evidence_rejects_empty_and_malformed():
    for bad in ("", "   ", "{}", "[]", "null", "not-json",
                '[{"step":"a"}]',                                        # 缺字段
                '[{"step":"a","tool":"t","key_numbers":[],"supports":"s"}]',  # 无数值证据
                '[{"step":"a","tool":"t","key_numbers":"x","supports":"s"}]'):
        steps, problem = parse_evidence_steps(bad)
        assert steps is None and problem, f"应被判为不合规: {bad!r}"


def test_parse_evidence_accepts_structured_steps():
    steps, problem = parse_evidence_steps(VALID_EVIDENCE)
    assert problem is None and len(steps) == 2


# ---- 报告接口不再接受空证据（P0 问题2 的第一道门）----

def test_report_submit_rejects_evidence_without_numbers(base_url, monkeypatch):
    _allow_active_run(monkeypatch, "gate-run")
    for bad in (BAD_EVIDENCE, "", "[]"):
        try:
            api(base_url, "/api/report/submit?run_id=gate-run&equip=P-101&conclusion=c"
                          f"&confidence=0.9&evidence_json={quote(bad, safe='')}")
            raise AssertionError(f"应 400：{bad!r}")
        except urllib.error.HTTPError as e:
            assert e.code == 400, f"{bad!r} 应为 400，实际 {e.code}"


def test_report_submit_accepts_structured_evidence(base_url, monkeypatch):
    _allow_active_run(monkeypatch, "gate-ok")
    d = api(base_url, "/api/report/submit?run_id=gate-ok&equip=P-101&conclusion=BPFO-defect&diagnosis_status=confirmed&diagnosis_code=bpfo"
                      f"&confidence=0.9&evidence_json={quote(VALID_EVIDENCE, safe='')}")
    assert d["evidence_steps"] == 5


# ---- 收尾门槛：达不到就不许记 done ----

def test_settle_marks_done_only_with_full_chain(ad_db, monkeypatch):
    status = _run_with(ad_db, monkeypatch)
    assert status["state"] == "done"
    assert status["verification"]["ok"] is True
    assert status["verification"]["tool_calls"] >= 5


def test_settle_marks_verification_failed_on_bad_evidence(ad_db, monkeypatch):
    status = _run_with(ad_db, monkeypatch, evidence=BAD_EVIDENCE)
    assert status["state"] == "verification_failed"
    assert status["failure_reason"] == "evidence_invalid"
    assert status["verification"]["evidence_structure"] is False


def test_settle_marks_verification_failed_when_index_unavailable(ad_db, monkeypatch):
    def broken(rid):
        raise RuntimeError("会话库里找不到这个 run")

    status = _run_with(ad_db, monkeypatch, index_provider=broken)
    assert status["state"] == "verification_failed"
    assert status["failure_reason"] == "index_failed"
    assert status["verification"]["index_built"] is False


def test_settle_marks_verification_failed_on_too_few_tool_calls(ad_db, monkeypatch):
    def provider(rid):
        return fake_index(rid, commands=[
            'curl.exe -s "http://127.0.0.1:8181/api/device"',
            f'curl.exe -s "http://127.0.0.1:8181/api/report/submit?run_id={rid}&conclusion=c"',
            f'curl.exe -s "http://127.0.0.1:8181/api/task/create?title=t&run_id={rid}"',
        ])

    status = _run_with(ad_db, monkeypatch, index_provider=provider)
    assert status["state"] == "verification_failed"
    assert status["failure_reason"] == "tool_calls_insufficient"
    assert status["verification"]["tool_calls"] == 3


def test_settle_marks_verification_failed_on_broken_linkage(ad_db, monkeypatch):
    # 索引声称 prompt 里没有 run_id → 业务半与执行半无法互相指认
    status = _run_with(ad_db, monkeypatch,
                       index_provider=lambda rid: fake_index(rid, run_id_in_prompt=False))
    assert status["state"] == "verification_failed"
    assert status["failure_reason"] == "linkage_failed"
    assert status["verification"]["linkage_errors"]


def test_partial_run_does_not_hold_back_recovery(ad_db, monkeypatch):
    """partial（有报告无工单）没有可关的工单：恢复后直接归位，不进入复测计数。"""
    def runner(prompt, run_id):
        ad_db.submit_report(run_id, "P-101", "外圈BPFO缺陷", 0.9, VALID_EVIDENCE, diagnosis_status="confirmed", diagnosis_code="bpfo")

    ad = _make(ad_db, runner=runner, monkeypatch=monkeypatch)
    ad.observe(_fault(), [dict(ALARM)])
    assert _settle(ad)["state"] == "partial"

    ad.observe({"fault": {"type": "none", "severity": 0.0}}, [])
    assert ad.status()["state"] == "idle"
    assert ad.status()["phases"]["task_created"] is False


def test_verification_payload_is_exposed_for_review(ad_db, monkeypatch):
    """评委要能看出"为什么不是 done"，所以核验明细必须在对外状态里。"""
    status = _run_with(ad_db, monkeypatch, evidence=BAD_EVIDENCE)
    v = status["verification"]
    for key in ("report", "evidence_structure", "task_linked", "index_built",
                "tool_calls", "tool_calls_ok", "linkage_ok", "problems", "messages"):
        assert key in v, f"核验明细缺字段 {key}"
    assert v["messages"] and "证据结构不合规" in v["messages"][0]


def test_status_does_not_leak_the_full_execution_index(ad_db, monkeypatch):
    """status 每 2s 轮询：核验明细可以外露，但整份执行索引只在内部用于落盘。"""
    status = _run_with(ad_db, monkeypatch)
    assert status["state"] == "done"
    assert "index" not in (status["verification"] or {})


def test_evidence_payload_round_trips_as_json(ad_db, monkeypatch):
    """落库证据仍是可解析数组（前端证据条数依赖它）。"""
    status = _run_with(ad_db, monkeypatch)
    rep = next(r for r in ad_db.report_list() if r["id"] == status["report_id"])
    assert isinstance(json.loads(rep["evidence"]), list)
