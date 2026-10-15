"""Persistent operator mode and mode-aware API safeguards."""
import importlib
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from conftest import api
from phmlite.agh_runtime import AGHError
from phmlite.server import app


def _operation_mode():
    try:
        return importlib.import_module("phmlite.operation_mode")
    except ImportError as exc:
        pytest.fail(f"persistent operation mode is missing: {exc}")


def _post_mode(base_url, mode, origin="http://127.0.0.1:8201"):
    request = urllib.request.Request(
        base_url + "/api/operation/mode",
        data=json.dumps({"mode": mode}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Origin": origin},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as response:
        return response.code, json.loads(response.read())


def _get_json(base_url, path, headers=None):
    request = urllib.request.Request(base_url + path, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as response:
        return response.code, json.loads(response.read())


def _login_operator(base_url):
    """M2：手动工单需要控制台凭据——登录换 token 供测试调用。"""
    body = json.dumps({"username": "admin", "password": "123456"}).encode()
    request = urllib.request.Request(base_url + "/api/login", data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())["token"]


def test_mode_defaults_to_agent_and_manual_choice_persists(tmp_path):
    mode = _operation_mode()

    assert mode.get_mode(tmp_path) == "agent"
    mode.set_mode(tmp_path, "manual")
    assert mode.get_mode(tmp_path) == "manual"
    assert json.loads((tmp_path / "operation-mode.json").read_text(encoding="utf-8"))["mode"] == "manual"


def test_invalid_mode_does_not_replace_persisted_choice(tmp_path):
    mode = _operation_mode()
    mode.set_mode(tmp_path, "manual")

    with pytest.raises(mode.OperationModeError):
        mode.set_mode(tmp_path, "local")

    assert mode.get_mode(tmp_path) == "manual"


def test_status_does_not_equate_agent_mode_with_provider_readiness(tmp_path):
    mode = _operation_mode()
    mode.invalidate_provider_probe()
    status = mode.get_status(tmp_path, agh_available=True, key_configured=True)
    assert status["mode"] == "agent"
    assert status["provider_ready"] is False and status["provider_status"] == "unverified"

    mode.record_provider_probe(True)
    ready = mode.get_status(tmp_path, agh_available=True, key_configured=True)
    assert ready["provider_ready"] is True and ready["provider_status"] == "ready"

    mode.record_provider_probe(False, "provider_unavailable")
    failed = mode.get_status(tmp_path, agh_available=True, key_configured=True)
    assert failed["provider_ready"] is False and failed["provider_status"] == "unavailable"


def test_operation_mode_api_persists_manual_and_requires_probe_to_return_to_agent(
    base_url, monkeypatch, tmp_path,
):
    import phmlite.server as server

    monkeypatch.setattr(app.state, "data_dir", tmp_path)
    monkeypatch.setattr(server, "get_agnes_key_status", lambda: {
        "configured": True, "profile": "test-profile",
    }, raising=False)
    monkeypatch.setattr(server, "agh_runtime_available", lambda: True, raising=False)
    probes = []

    def fail_probe():
        probes.append("failed")
        raise AGHError("provider_unavailable", "fake test error")

    monkeypatch.setattr(server, "probe_provider", fail_probe, raising=False)
    status, payload = _post_mode(base_url, "manual")
    assert status == 200 and payload["mode"] == "manual"
    assert _operation_mode().get_mode(tmp_path) == "manual"

    status, _ = _post_mode(base_url, "agent")
    assert status == 503 and probes == ["failed"]
    assert _operation_mode().get_mode(tmp_path) == "manual"
    assert api(base_url, "/api/operation/mode")["mode"] == "manual"

    monkeypatch.setattr(server, "probe_provider", lambda: probes.append("ok"), raising=False)
    status, payload = _post_mode(base_url, "agent")
    assert status == 200 and payload["mode"] == "agent" and probes[-1] == "ok"
    assert api(base_url, "/api/operation/mode")["provider_ready"] is True


def test_operation_mode_write_requires_loopback_same_origin(base_url, monkeypatch, tmp_path):
    import phmlite.server as server

    monkeypatch.setattr(app.state, "data_dir", tmp_path)
    status, _ = _post_mode(base_url, "manual", origin="http://attacker.example")

    assert status == 403
    assert _operation_mode().get_mode(tmp_path) == "agent"


def test_agent_mode_requires_runtime_before_provider_probe(base_url, monkeypatch, tmp_path):
    import phmlite.server as server

    monkeypatch.setattr(app.state, "data_dir", tmp_path)
    monkeypatch.setattr(server, "agh_runtime_available", lambda: False, raising=False)
    probes = []
    monkeypatch.setattr(server, "probe_provider", lambda: probes.append(True), raising=False)
    _operation_mode().set_mode(tmp_path, "manual")

    status, payload = _post_mode(base_url, "agent")

    assert status == 503 and "AGH 运行时离线" in payload["detail"]
    assert probes == [] and _operation_mode().get_mode(tmp_path) == "manual"


def test_manual_mode_marks_manual_work_orders_and_rejects_agent_artifacts(
    base_url, monkeypatch, tmp_path,
):
    import phmlite.server as server

    monkeypatch.setattr(app.state, "data_dir", tmp_path)
    _operation_mode().set_mode(tmp_path, "manual")
    before_tasks = len(app.state.db.task_list())
    before_reports = len(app.state.db.report_list())

    status, _ = _get_json(base_url, "/api/task/create?title=agent&desc=test&level=severe&source=agent")
    assert status == 409 and len(app.state.db.task_list()) == before_tasks

    # M2：人工工单需控制台凭据——无凭据 403，带登录 token 才创建
    status, _ = _get_json(base_url, "/api/task/create?title=manual&desc=test&level=notice&source=manual")
    assert status == 403 and len(app.state.db.task_list()) == before_tasks
    token = _login_operator(base_url)
    status, task = _get_json(base_url, "/api/task/create?title=manual&desc=test&level=notice&source=manual",
                             headers={"X-Operator-Token": token})
    assert status == 200 and app.state.db.get_task(task["id"])["source"] == "manual"

    query = urllib.parse.urlencode({
        "run_id": "manual-mode-test", "equip": "P-101", "conclusion": "manual must not create diagnosis",
        "confidence": 0.9, "evidence_json": "{}",
    })
    status, _ = _get_json(base_url, "/api/report/submit?" + query)
    assert status == 409 and len(app.state.db.report_list()) == before_reports
