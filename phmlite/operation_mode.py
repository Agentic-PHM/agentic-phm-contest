"""Persist the operator-selected agent/manual mode and cache provider readiness."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.request
from pathlib import Path


class OperationModeError(RuntimeError):
    """Invalid or unreadable persisted operating mode."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


_MODES = {"agent", "manual"}
_MODE_FILENAME = "operation-mode.json"
_PROVIDER_LOCK = threading.Lock()
_PROVIDER_RESULT = None
_PROVIDER_CATEGORY = None


def _mode_path(data_dir) -> Path:
    return Path(data_dir) / _MODE_FILENAME


def get_mode(data_dir) -> str:
    """Read the selected mode; a missing file starts in agent-selected mode."""
    path = _mode_path(data_dir)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "agent"
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise OperationModeError("mode_unavailable", "运行模式配置无法读取。") from None
    mode = saved.get("mode") if isinstance(saved, dict) else None
    if mode not in _MODES:
        raise OperationModeError("mode_invalid", "运行模式配置无效。")
    return mode


def set_mode(data_dir, mode: str) -> None:
    """Atomically persist an explicit operator choice."""
    if mode not in _MODES:
        raise OperationModeError("mode_invalid", "运行模式只支持 agent 或 manual。")
    directory = Path(data_dir)
    temp_name = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".operation-mode-", suffix=".tmp", dir=str(directory))
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            json.dump({"version": 1, "mode": mode}, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, _mode_path(directory))
    except OSError:
        raise OperationModeError("mode_write_failed", "运行模式无法安全保存。") from None
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def invalidate_provider_probe() -> None:
    global _PROVIDER_RESULT, _PROVIDER_CATEGORY
    with _PROVIDER_LOCK:
        _PROVIDER_RESULT = None
        _PROVIDER_CATEGORY = None


def record_provider_probe(available: bool, category: str | None = None) -> None:
    global _PROVIDER_RESULT, _PROVIDER_CATEGORY
    with _PROVIDER_LOCK:
        _PROVIDER_RESULT = bool(available)
        _PROVIDER_CATEGORY = None if available else (category or "provider_unavailable")


def agh_runtime_available(timeout: float = 0.5) -> bool:
    """Fast daemon health check only; this does not call a model or inspect credentials."""
    try:
        port = int(os.environ.get("AGH_PORT", "4178"))
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def get_status(data_dir, *, agh_available: bool, key_configured: bool) -> dict:
    mode = get_mode(data_dir)
    with _PROVIDER_LOCK:
        result = _PROVIDER_RESULT
        category = _PROVIDER_CATEGORY

    if not agh_available:
        provider_status = "runtime_offline"
    elif not key_configured:
        provider_status = "key_missing"
    elif result is True:
        provider_status = "ready"
    elif result is False:
        provider_status = "unavailable"
    else:
        provider_status = "unverified"
    return {
        "mode": mode,
        "agh_available": bool(agh_available),
        "key_configured": bool(key_configured),
        "provider_ready": provider_status == "ready",
        "provider_status": provider_status,
        "provider_reason": category,
    }
