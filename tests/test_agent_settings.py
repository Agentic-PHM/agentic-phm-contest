"""Local Agnes credential storage and same-origin settings endpoints."""
import importlib
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from starlette.requests import Request


FAKE_KEY = "FAKE_KEY_never_return_or_log_7x"
CREDENTIAL_REF = "secret://agnes-ai/account-0123456789abcdef01234567-r1"


def _settings_module():
    try:
        return importlib.import_module("phmlite.agent_settings")
    except ImportError as exc:
        pytest.fail(f"local AGH settings module is missing: {exc}")


def _profile(tmp_path, monkeypatch, credential_ref=CREDENTIAL_REF, secret=None):
    home = tmp_path / "agh-home"
    profile = "test-profile"
    config_dir = home / "profiles" / profile
    secrets_dir = home / "secrets" / "agnes-ai"
    config_dir.mkdir(parents=True)
    secrets_dir.mkdir(parents=True)
    config = {
        "version": 1,
        "profile": profile,
        "accounts": [{
            "id": "agnes-ai",
            "accountId": "account-test",
            "enabled": True,
            "credentialRef": credential_ref,
            "model": "agnes-test-model",
        }],
        "defaultAccountId": "account-test",
    }
    (config_dir / "configuration.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("AGH_HOME", str(home))
    monkeypatch.setenv("PHM_AGH_PROFILE", profile)
    secret_path = secrets_dir / CREDENTIAL_REF.rsplit("/", 1)[-1]
    if secret is not None:
        secret_path.write_text(json.dumps({
            "version": 1, "kind": "api-key", "provider": "agnes-ai", "value": secret,
        }), encoding="utf-8")
    return home, secret_path


def _request(base_url, path, method="GET", payload=None, origin="http://127.0.0.1:8201"):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Origin": origin}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as response:
        return response.code, json.loads(response.read())


def _request_for_guard(client, host, origin, scheme="http"):
    headers = [(b"host", host.encode("ascii"))]
    if origin is not None:
        headers.append((b"origin", origin.encode("ascii")))
    return Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": scheme, "path": "/api/settings/agnes-key",
        "raw_path": b"/api/settings/agnes-key", "query_string": b"", "headers": headers,
        "client": client, "server": ("127.0.0.1", 8181),
    })


def test_key_status_reports_only_presence_and_profile(tmp_path, monkeypatch):
    settings = _settings_module()
    _profile(tmp_path, monkeypatch, secret=FAKE_KEY)

    status = settings.get_agnes_key_status()

    assert status == {"configured": True, "profile": "test-profile"}
    assert FAKE_KEY not in repr(status)


def test_save_key_follows_the_active_agnes_credential_reference(tmp_path, monkeypatch):
    settings = _settings_module()
    _, secret_path = _profile(tmp_path, monkeypatch)
    protected = []
    def protect_empty_temp(path):
        assert path.read_bytes() == b""
        protected.append(path)

    monkeypatch.setattr(settings, "_protect_private_file", protect_empty_temp)

    settings.save_agnes_api_key(FAKE_KEY)

    stored = json.loads(secret_path.read_text(encoding="utf-8"))
    assert stored["version"] == 1 and stored["kind"] == "api-key" and stored["provider"] == "agnes-ai"
    assert stored["value"] == FAKE_KEY
    assert protected and protected[0] != secret_path and protected[0].parent == secret_path.parent
    assert settings.get_agnes_key_status()["configured"] is True


def test_invalid_credential_reference_cannot_escape_provider_secret_directory(tmp_path, monkeypatch):
    settings = _settings_module()
    home, _ = _profile(tmp_path, monkeypatch, credential_ref="secret://agnes-ai/%2e%2e/outside")

    with pytest.raises(settings.AgentSettingsError):
        settings.save_agnes_api_key(FAKE_KEY)

    assert not (home / "secrets" / "outside").exists()


def test_acl_failure_preserves_the_existing_key_and_removes_temporary_file(tmp_path, monkeypatch):
    settings = _settings_module()
    _, secret_path = _profile(tmp_path, monkeypatch, secret="FAKE_OLD_KEY")
    previous = secret_path.read_bytes()

    def fail_protection(path):
        raise settings.AgentSettingsError("acl_failed", "private file ACL setup failed")

    monkeypatch.setattr(settings, "_protect_private_file", fail_protection)
    with pytest.raises(settings.AgentSettingsError):
        settings.save_agnes_api_key(FAKE_KEY)

    assert secret_path.read_bytes() == previous
    assert not list(secret_path.parent.glob(".agnes-key-*.tmp"))


def test_acl_is_granted_before_inheritance_is_removed(tmp_path, monkeypatch):
    settings = _settings_module()
    commands = []
    monkeypatch.setattr(settings, "_current_windows_user", lambda: "AFU\\Administrator")
    monkeypatch.setattr(settings, "_run_acl_command", lambda args: commands.append(args))
    path = tmp_path / "secret.tmp"
    path.write_bytes(b"")

    settings._protect_private_file(path)

    assert [args[2] for args in commands] == ["/grant", "/inheritance:r", "/grant"]
    assert all("AFU\\Administrator:F" in args and "NT AUTHORITY\\SYSTEM:F" in args
               for args in (commands[0], commands[2]))


def test_write_routes_reject_cross_origin_and_non_loopback_requests():
    settings = _settings_module()

    for request in (
        _request_for_guard(("127.0.0.1", 1234), "127.0.0.1:8181", "http://attacker.example"),
        _request_for_guard(("192.168.1.20", 1234), "127.0.0.1:8181", "http://127.0.0.1:8181"),
        _request_for_guard(("127.0.0.1", 1234), "attacker.example", "http://attacker.example"),
        _request_for_guard(("127.0.0.1", 1234), "127.0.0.1:8181", None),
    ):
        with pytest.raises(settings.AgentSettingsError):
            settings.require_local_same_origin(request)

    settings.require_local_same_origin(
        _request_for_guard(("127.0.0.1", 1234), "127.0.0.1:8181", "http://127.0.0.1:8181")
    )


def test_settings_status_endpoint_never_returns_the_key(base_url, monkeypatch):
    import phmlite.server as server

    monkeypatch.setattr(server, "get_agnes_key_status", lambda: {
        "configured": True, "profile": "test-profile", "value": FAKE_KEY,
    }, raising=False)

    status, payload = _request(base_url, "/api/settings/agnes-key")

    assert status == 200
    assert payload == {"configured": True, "profile": "test-profile"}
    assert FAKE_KEY not in json.dumps(payload)


def test_settings_write_endpoint_accepts_same_origin_and_never_echoes_key(base_url, monkeypatch):
    import phmlite.server as server

    saved = []
    monkeypatch.setattr(server, "save_agnes_api_key", lambda value: saved.append(value), raising=False)

    status, payload = _request(base_url, "/api/settings/agnes-key", "POST", {"api_key": FAKE_KEY})

    assert status == 200 and payload["ok"] is True
    assert saved == [FAKE_KEY]
    assert FAKE_KEY not in json.dumps(payload)


def test_settings_write_endpoint_rejects_cross_origin(base_url, monkeypatch):
    import phmlite.server as server

    saved = []
    monkeypatch.setattr(server, "save_agnes_api_key", lambda value: saved.append(value), raising=False)

    status, payload = _request(base_url, "/api/settings/agnes-key", "POST", {"api_key": FAKE_KEY},
                               origin="http://attacker.example")

    assert status == 403 and saved == []
    assert FAKE_KEY not in json.dumps(payload)


def test_settings_probe_endpoint_uses_shared_agh_probe(base_url, monkeypatch):
    import phmlite.server as server

    probes = []
    monkeypatch.setattr(server, "probe_provider", lambda timeout=45: probes.append(timeout), raising=False)

    status, payload = _request(base_url, "/api/settings/agnes-key/probe", "POST", {})

    assert status == 200 and payload["ok"] is True
    assert probes == [45]
