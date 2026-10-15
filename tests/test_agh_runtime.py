"""Shared AGH CLI boundary: safe errors, profile selection, and provider probes."""
import importlib
import json
import subprocess

import pytest


def _runtime_module():
    try:
        return importlib.import_module("phmlite.agh_runtime")
    except ImportError as exc:
        pytest.fail(f"shared AGH runtime entry point is missing: {exc}")


def _install_fake_runtime(tmp_path, monkeypatch):
    agh_dir = tmp_path / "agh"
    node = agh_dir / "node24" / "node.exe"
    entry = agh_dir / "runtime" / "agnes.mjs"
    node.parent.mkdir(parents=True)
    entry.parent.mkdir(parents=True)
    node.write_bytes(b"")
    entry.write_text("", encoding="utf-8")
    monkeypatch.setenv("AGH_DIR", str(agh_dir))
    monkeypatch.setenv("AGH_HOME", str(tmp_path / "agh-home"))
    monkeypatch.setenv("PHM_AGH_PROFILE", "test-profile")
    return agh_dir, node, entry


def test_run_prompt_uses_configured_profile_without_shell(tmp_path, monkeypatch):
    runtime = _runtime_module()
    agh_dir, node, entry = _install_fake_runtime(tmp_path, monkeypatch)
    observed = {}

    def run(args, **kwargs):
        observed.update(args=args, **kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="诊断答复", stderr="")

    monkeypatch.setattr(runtime.subprocess, "run", run)
    result = runtime.run_prompt("检查振动趋势", timeout=17, mode="json")

    assert result == "诊断答复"
    assert observed["args"] == [
        str(node), str(entry), "--profile", "test-profile", "--cwd", str(runtime.REPO_ROOT),
        "-p", "检查振动趋势", "--mode", "json", "--standalone",
    ]
    assert observed["cwd"] == str(agh_dir)
    assert observed["env"]["AGH_HOME"] == str(tmp_path / "agh-home")
    assert observed.get("shell", False) is False
    assert observed["timeout"] == 17


def test_run_prompt_hides_process_output_on_failure(tmp_path, monkeypatch):
    runtime = _runtime_module()
    _install_fake_runtime(tmp_path, monkeypatch)

    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 9, stdout="FAKE_SECRET_91", stderr="FAKE_SECRET_91")

    monkeypatch.setattr(runtime.subprocess, "run", run)
    with pytest.raises(runtime.AGHError) as caught:
        runtime.run_prompt("检查", timeout=5)

    assert "FAKE_SECRET_91" not in str(caught.value)
    assert "9" in str(caught.value)


def test_run_prompt_rejects_missing_runtime(tmp_path, monkeypatch):
    runtime = _runtime_module()
    monkeypatch.setenv("AGH_DIR", str(tmp_path / "missing-agh"))
    monkeypatch.setenv("AGH_HOME", str(tmp_path / "agh-home"))

    with pytest.raises(runtime.AGHError, match="未找到"):
        runtime.run_prompt("检查", timeout=5)


def test_run_prompt_timeout_is_a_safe_agh_error(tmp_path, monkeypatch):
    runtime = _runtime_module()
    _install_fake_runtime(tmp_path, monkeypatch)

    def run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 5, output="FAKE_SECRET_92", stderr="FAKE_SECRET_92")

    monkeypatch.setattr(runtime.subprocess, "run", run)
    with pytest.raises(runtime.AGHError) as caught:
        runtime.run_prompt("检查", timeout=5)

    assert "FAKE_SECRET_92" not in str(caught.value)
    assert "超时" in str(caught.value)


def test_probe_provider_requires_an_ok_provider_report(tmp_path, monkeypatch):
    runtime = _runtime_module()
    _, node, entry = _install_fake_runtime(tmp_path, monkeypatch)
    observed = {}

    def run(args, **kwargs):
        observed.update(args=args, **kwargs)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps([
            {"name": "provider", "status": "ok", "detail": []}
        ]), stderr="")

    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime.probe_provider(timeout=12) is None
    assert observed["args"] == [
        str(node), str(entry), "doctor", "provider", "--probe", "--json",
        "--profile", "test-profile", "--cwd", str(runtime.REPO_ROOT),
    ]
    assert observed.get("shell", False) is False
    assert observed["timeout"] == 12


def test_probe_provider_rejects_invalid_or_failed_report(tmp_path, monkeypatch):
    runtime = _runtime_module()
    _install_fake_runtime(tmp_path, monkeypatch)

    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps([
            {"name": "provider", "status": "fail", "detail": ["FAKE_SECRET_93"]}
        ]), stderr="")

    monkeypatch.setattr(runtime.subprocess, "run", run)
    with pytest.raises(runtime.AGHError) as caught:
        runtime.probe_provider()

    assert "FAKE_SECRET_93" not in str(caught.value)


def test_autodiag_runner_delegates_prompt_to_shared_runtime(tmp_path, monkeypatch):
    import phmlite.autodiag as autodiag

    calls = []
    monkeypatch.setattr(autodiag, "run_prompt", lambda prompt, **kwargs: calls.append((prompt, kwargs)) or "ok",
                        raising=False)
    diag = autodiag.AutoDiag(db=object(), data_dir=tmp_path, runner=lambda *args: None)
    monkeypatch.setattr(diag, "_health_check", lambda: None)

    diag._run_agh("取证指令", "auto-test")

    assert calls == [("取证指令", {"timeout": diag.budget})]
    # P0 防泄漏：提示词原文落盘（独立评分器据此审计"提示词无真值"），结果行追加在后
    log = (tmp_path / "autodiag" / "auto-test.log").read_text(encoding="utf-8")
    assert log == "PROMPT:\n取证指令\nAGH session completed; details remain in the AGH audit log.\n"
