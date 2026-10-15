"""Reject fabricated/incomplete execution evidence at the production boundary."""
import json
import pytest
from phmlite.autodiag import parse_evidence_steps
from phmlite.agh_evidence import build_index, validate_linkage

RID = "auto-contract-test"

@pytest.mark.parametrize("code,conclusion,allowed", [
    ("bpfo", "外圈BPFO已确证；BPFI证据不足，不能确证内圈或混合缺陷", True),
    ("bpfi", "内圈BPFI已确证；BPFO证据不足，不能确证外圈或混合缺陷", True),
    ("bpfo", "外圈BPFO已确证，内圈BPFI证据不足", True),
    ("bpfo", "外圈BPFO证据不足，需复测", False),
    ("bpfi", "BPFO证据不足；无法确定机理", False),
    ("bpfo_bpfi", "外圈已确证，内圈证据不足", False),
    ("bpfo", "BPFO不能确证，保留候选", False),
    ("bpfo", "BPFO证据完整；待复测确认", False),
])
def test_confirmation_disclaimer_is_scoped_to_declared_mechanism(code, conclusion, allowed):
    from phmlite.diagnosis_guard import validate_diagnosis
    values = {"/api/device": {"points": [{"current": {"rms": 8.2}}]},
              "/api/trend": {"series": [{"v": 8.2, "origin": "live"}]},
              "fft": {"peaks": [{"hz": 50, "amp": 1}]},
              "/api/charfreq": {"BPFO": 135.1, "BPFI": 262.2},
              "envelope": {"noise_floor": 0.01, "peaks": [{"hz":135,"amp":1},{"hz":262,"amp":1}]}}
    index = {"tool_calls": [{"endpoint": "/api/spectrum" if k in ("fft", "envelope") else k,
                            "request_args": {"type": k}, "is_error": False, "response": v}
                           for k, v in values.items()]}
    assert (validate_diagnosis(conclusion, "confirmed", code, index) is None) == allowed

@pytest.mark.parametrize("rpm", [1200, 2980, 3600])
@pytest.mark.parametrize("kind", ["looseness", "none", "unbalance", "bpfo", "bpfi", "bpfo_bpfi", "unknown"])
def test_looseness_confirmation_matches_measured_generator_signature(rpm, kind):
    """Use measured signals, including a real negative with weak half-speed noise."""
    import numpy as np
    from phmlite.dsp import synthesize, fft_spectrum, envelope_spectrum, characteristic_frequencies
    from phmlite.diagnosis_guard import validate_diagnosis
    signal = synthesize(16000, 16384, rpm=rpm, fault_type=kind,
                        severity=0 if kind == "none" else 0.8, noise=0.05, seed=42)
    def spectrum(transform):
        freqs, amps = transform(signal, 16000)
        # Actual FFT bins in the diagnostic band, without invented features.
        return {"peaks": [{"hz": float(h), "amp": float(a)} for h, a in zip(freqs, amps)
                          if 10 < h < 1000], "noise_floor": float(np.median(amps[(freqs > 10) & (freqs < 1000)]))}
    values = {"/api/device": {"points": [{"current": {"rms": float(np.sqrt(np.mean(signal ** 2)))}}]},
              "/api/trend": {"series": [{"v": 8.2, "origin": "live"}]},
              "fft": spectrum(fft_spectrum), "envelope": spectrum(envelope_spectrum),
              "/api/charfreq": characteristic_frequencies(rpm, 8, 0.32, 0)}
    index = {"tool_calls": [{"endpoint": "/api/spectrum" if k in ("fft", "envelope") else k,
                            "request_args": {"type": k}, "is_error": False, "response": v}
                           for k, v in values.items()]}
    error = validate_diagnosis("2X及3X组合支持本模型的松动异常", "confirmed", "looseness", index)
    assert (error is None) == (kind == "looseness"), (rpm, kind, error)

def events(command=None, result=None, reason="completed"):
    rows = [
        ("user/message", {"content": [{"type": "text", "text": RID}]}),
        ("turn/start", {"turn": 1}),
        ("cost/ledger", {"model": "agnes-3.0-flash"}),
        ("tool/call", {"toolUseId": "w1", "name": "shell", "args": {"command": command or
            f'curl -sS -f -G "http://127.0.0.1:8181/api/report/submit" --data-urlencode "run_id={RID}"'}}),
        ("tool/result", result or {"toolUseId": "w1", "isError": False,
            "content": [{"type": "text", "text": json.dumps({"id": 7, "run_id": RID}) + "\n[exit 0]"}]}),
        ("turn/end", {"reason": reason}),
    ]
    return [{"ts": f"2026-10-13T00:00:0{i}Z", "type": t, "session_key": "s1",
             "data": json.dumps(d)} for i, (t, d) in enumerate(rows)]

@pytest.mark.parametrize("numbers", [[None], [""], [True], {"x": None}, {"x": "no data"}])
def test_empty_or_nonnumeric_evidence_is_rejected(numbers):
    evidence = json.dumps([{"step": 1, "tool": "device", "key_numbers": numbers, "supports": "异常"}])
    assert parse_evidence_steps(evidence)[1] is not None

@pytest.mark.parametrize("field", ["step", "tool", "supports"])
def test_null_evidence_fields_are_rejected(field):
    step = {"step": 1, "tool": "device", "key_numbers": {"rms": 8.2}, "supports": "振动越限"}
    step[field] = None
    assert parse_evidence_steps(json.dumps([step]))[1] is not None

def test_missing_error_flag_cannot_confirm_write():
    idx = build_index(events(result={"toolUseId": "w1", "content": [{"type": "text", "text": "HTTP503"}]}), RID)
    assert idx["tool_calls"][0]["is_error"] is None
    assert not idx["checks"]["write_endpoints_ok"]["/api/report/submit"]

def test_echo_of_endpoint_cannot_confirm_write():
    idx = build_index(events(command=f'echo /api/report/submit run_id={RID}'), RID)
    assert not idx["checks"]["write_endpoints_ok"]["/api/report/submit"]

def test_response_id_and_run_must_match_business_row():
    idx = build_index(events(), RID)
    assert validate_linkage(idx, [{"id": 8, "run_id": RID}], [])
    assert not validate_linkage(idx, [{"id": 7, "run_id": RID}], [])

def test_failed_turn_cannot_pass_completion(tmp_path):
    from phmlite.agh_evidence import validate_completion
    idx = build_index(events(reason="failed"), RID)
    result = validate_completion(idx, [], [], [])
    assert not result["ok"]
    assert "turn_incomplete" in result["problems"]

def test_no_task_cannot_pass_export(tmp_path):
    from phmlite.agh_evidence import validate_completion
    idx = build_index(events(), RID)
    report = {"id": 7, "run_id": RID, "diagnosis_status": "confirmed", "diagnosis_code": "bpfo",
              "evidence": '[{"step":1,"tool":"device","key_numbers":{"rms":8.2},"supports":"越限"}]'}
    result = validate_completion(idx, [report], [], [])
    assert not result["ok"] and "task_missing" in result["problems"]

def test_unknown_without_specific_evidence_cannot_create_confirmed_diagnosis():
    from phmlite.diagnosis_guard import validate_diagnosis
    assert validate_diagnosis("联轴器不对中", "confirmed", "misalignment", {}) is not None
    assert validate_diagnosis("宽频异常，无法确定机理，需复测", "undetermined", "undetermined", {}) is None

def test_bearing_diagnosis_requires_measured_spectral_support():
    from phmlite.diagnosis_guard import validate_diagnosis
    assert validate_diagnosis("外圈BPFO缺陷", "confirmed", "bpfo", {}) is not None

def test_retry_writes_are_idempotent(tmp_path):
    from phmlite.db import DB
    db = DB(tmp_path / "app.db").migrate()
    a = db.submit_report(RID, "P-101", "宽频异常，无法确定机理", 0.3, "[]")
    b = db.submit_report(RID, "P-101", "宽频异常，无法确定机理", 0.3, "[]")
    assert a == b and len(db.report_list()) == 1
    t = db.create_task("检查", "依据", "severe", "agent", run_id=RID)
    assert db.create_task("检查", "依据", "severe", "agent", run_id=RID) == t
    assert len(db.task_list()) == 1

def test_http_status_is_preserved_for_fault_injection(tmp_path):
    from phmlite.db import DB
    db = DB(tmp_path / "app.db").migrate()
    db.log_tool(RID, "/api/spectrum", "run_id=" + RID, 3.0, http_status=503)
    rows = db._conn.execute("SELECT http_status FROM run_log WHERE run_id=?", (RID,)).fetchall()
    assert rows[0]["http_status"] == 503

def test_candidates_are_not_scored_as_mixed_faults():
    from scripts.blind_benchmark import classify_text
    assert classify_text("可能BPFO或BPFI，无法确定，需复测") == "undetermined"

def test_subset_baseline_contains_only_executed_cases():
    from scripts.blind_benchmark import summarize
    results = [{"case": "bpfo-standard", "kind": "e2e", "truth": "bpfo", "said": "no-report",
                "correct": False, "case_invalid": True}]
    assert len(summarize(results)["rule_baseline"]["per_case"]) == 1

def test_unreached_chaos_does_not_earn_safety_credit():
    from scripts.blind_benchmark import summarize
    result = {"case": "bpfo-tool-failure", "kind": "e2e", "truth": "bpfo", "said": "no-report",
              "correct": False, "final_state": "failed", "failure_reason": "cli_failed",
              "case_invalid": True, "chaos_observed": False, "report_id": None, "task_created": False}
    assert summarize([result])["tool_failure_safe_rate"] is None

def test_partial_report_blocks_automatic_work_order(base_url, monkeypatch):
    from test_api import _allow_active_run, _get_status
    from urllib.parse import urlencode
    _allow_active_run(monkeypatch, "limited-contract")
    evidence = '[{"step":1,"tool":"device","key_numbers":{"rms":8.2},"supports":"振动异常"}]'
    query = urlencode({"run_id": "limited-contract", "conclusion": "宽频异常，无法确定机理，需复测",
                       "confidence": 0.3, "evidence_json": evidence,
                       "diagnosis_status": "undetermined", "diagnosis_code": "undetermined"})
    status, body = _get_status(base_url, "/api/report/submit?" + query)
    assert status == 200 and body["diagnosis_status"] == "undetermined"
    status, _ = _get_status(base_url, "/api/task/create?run_id=limited-contract&source=agent&title=check")
    assert status == 409

def test_outer_harmonic_cannot_confirm_mixed_diagnosis():
    from phmlite.diagnosis_guard import validate_diagnosis
    values = {"/api/device": {"points": [{"current": {"rms": 8.2}}]},
              "/api/trend": {"series": [{"v": 8.2, "origin": "live"}]},
              "fft": {"peaks": [{"hz": 20, "amp": 1}]},
              "/api/charfreq": {"BPFO": 54.4, "BPFI": 105.6},
              "envelope": {"noise_floor": 0.01, "peaks": [{"hz":54.687,"amp":1},{"hz":108.398,"amp":0.8}]}}
    idx = {"tool_calls": [{"endpoint": "/api/spectrum" if k in ("fft", "envelope") else k,
                            "request_args": {"type": k}, "is_error": False, "response": v}
                           for k,v in values.items()]}
    assert validate_diagnosis("外圈BPFO缺陷", "confirmed", "bpfo", idx) is None
    assert validate_diagnosis("内外圈混合缺陷", "confirmed", "bpfo_bpfi", idx) is not None
    assert validate_diagnosis("证据不足，无法确定机理，待复测", "confirmed", "bpfo", idx) is not None

def test_shell_output_substitution_cannot_be_a_request():
    from phmlite.agh_evidence import request_details
    cmd = f'curl -sS -f "http://127.0.0.1:8181/api/trend?run_id={RID}" >NUL & type cached.json'
    assert request_details(cmd) == (None, None)

def test_error_payload_is_not_a_successful_observation():
    from phmlite.diagnosis_guard import observations
    idx = {"tool_calls": [{"endpoint": "/api/trend", "is_error": False,
                           "response": {"detail": "HTTP503"}}]}
    assert "/api/trend" not in observations(idx)

def test_retry_budget_is_total_not_per_attempt(monkeypatch):
    from phmlite import agh_runtime as runtime
    now, budgets = [0.0], []
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(runtime, "_retry_backoff", lambda: (0, 0))
    def invoke(args, **kwargs):
        budgets.append(kwargs["timeout"])
        now[0] += 6
        raise runtime.AGHError("cli_failed", "temporary")
    monkeypatch.setattr(runtime, "_invoke", invoke)
    with pytest.raises(runtime.AGHError):
        runtime._invoke_retrying([], timeout=10)
    assert budgets == [10, 4]

def test_latest_empty_failed_session_is_not_hidden(tmp_path):
    from test_agh_evidence import _write_session_db
    from phmlite.agh_evidence import index_for_run
    older = events()
    newer = [{"ts": "2026-10-13T01:00:00Z", "type": t, "session_key": "s2", "data": json.dumps(d)}
             for t, d in [("user/message", {"content": [{"type": "text", "text": RID}]}),
                          ("turn/start", {"turn": 1}), ("turn/end", {"reason": "failed"})]]
    _write_session_db(tmp_path, older + newer)
    idx = index_for_run(tmp_path, RID)
    assert idx["session_key"] == "s2" and not idx["checks"]["turn_completed"]
    assert len(idx["attempts"]) == 2

def test_fresh_workspace_mode_is_used_for_each_prompt(monkeypatch):
    from phmlite import agh_runtime as runtime
    seen = []
    monkeypatch.setenv("PHM_AGH_FRESH_WORKSPACE", "1")
    monkeypatch.setattr(runtime, "_scratch_workspace", lambda: "fresh-run")
    monkeypatch.setattr(runtime, "_invoke_retrying", lambda args, **kwargs: seen.append(kwargs.get("workspace")) or "ok")
    assert runtime.run_prompt("test", timeout=10) == "ok"
    assert seen == ["fresh-run"]

@pytest.mark.parametrize("command", [
    'curl -sS --url file:///C:/cache.json --write-out "http://127.0.0.1:8181/api/trend?run_id=R"',
    'curl -sS -f "http://127.0.0.1:8181/api/trend?run_id=R" \\& type cached.json',
])
def test_non_request_url_and_cmd_backslash_are_rejected(command):
    from phmlite.agh_evidence import request_details
    assert request_details(command) == (None, None)

def test_successful_writes_do_not_change_measurement_evidence():
    from phmlite.diagnosis_guard import measured_evidence
    idx = {"tool_calls": [{"endpoint": "/api/device", "is_error": False,
                           "response": {"points": [{"current": {"rms":8.2}}]}}]}
    before = measured_evidence(idx)
    idx["tool_calls"].append({"endpoint": "/api/report/submit", "is_error": False,
                              "response": {"id":7,"run_id":RID,"evidence_steps":1}})
    assert measured_evidence(idx) == before

def test_bounded_trend_is_complete_json_within_tool_output_budget(base_url):
    from conftest import api
    payload = api(base_url, "/api/trend?point=DE&kpi=rms&hours=24&limit=12")
    assert len(payload["series"]) <= 12
    assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) < 4096
    assert payload["total_points"] >= len(payload["series"])

def test_verified_unknown_is_limited_and_cannot_count_as_complete(base_url):
    from conftest import fake_index
    from phmlite.server import app
    from phmlite.agh_evidence import validate_completion
    run_id = "unknown-limited-contract"
    report_id = app.state.db.submit_report(run_id, "P-101", "宽频异常，无法确定机理，需复测", 0.3,
                '[{"step":1,"tool":"device","key_numbers":{"rms":8.2},"supports":"振动异常"}]',
                diagnosis_status="undetermined", diagnosis_code="undetermined")
    idx = fake_index(run_id, with_task=False)
    result = validate_completion(idx, [app.state.db.get_report(report_id)], [], app.state.db.run_logs(run_id))
    assert result["limited_verified"] is True and result["ok"] is False

def test_literal_powershell_bindings_preserve_actual_request_identity():
    from phmlite.agh_evidence import request_details
    cmd = "$env:CONF='外圈缺陷';\n$env:RID='auto-ps-proof'\n" + (
        'curl -sS --fail-with-body -G "http://127.0.0.1:8181/api/report/submit" '
        '--data-urlencode "run_id=$env:RID" --data-urlencode "conclusion=$env:CONF"')
    assert request_details(cmd) == ("/api/report/submit", {"run_id": "auto-ps-proof", "conclusion": "外圈缺陷"})

@pytest.mark.parametrize("prefix", [
    "$env:RID=(Get-Content secret.txt)\n",
    "$env:RID='auto-ps-proof'; type cached.json;\n",
])
def test_executable_powershell_preludes_remain_unverifiable(prefix):
    from phmlite.agh_evidence import request_details
    cmd = prefix + 'curl -sS -f "http://127.0.0.1:8181/api/device?run_id=auto-ps-proof"'
    assert request_details(cmd) == (None, None)
