"""Resolve and securely update the active AGH Agnes credential."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit


class AgentSettingsError(RuntimeError):
    """Safe settings failure; details never contain submitted Key material."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


_PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_CREDENTIAL_RE = re.compile(r"^account-[A-Za-z0-9-]+-r[0-9]+$")
_MAX_SECRET_BYTES = 64 * 1024


def _agh_location():
    agh_dir = Path(os.environ.get("AGH_DIR", r"D:\agh-contest"))
    home = Path(os.environ.get("AGH_HOME", str(agh_dir / "agh-home"))).resolve(strict=False)
    profile = os.environ.get("PHM_AGH_PROFILE", "local-dev").strip()
    if not _PROFILE_RE.fullmatch(profile) or profile in {".", ".."}:
        raise AgentSettingsError("profile_invalid", "AGH profile 配置无效。")
    return home, profile


def _active_credential_path():
    home, profile = _agh_location()
    profile_dir = home / "profiles" / profile
    config_path = profile_dir / "configuration.json"
    try:
        if profile_dir.resolve(strict=False) != profile_dir or config_path.is_symlink():
            raise AgentSettingsError("profile_invalid", "AGH profile 配置路径无效。")
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except AgentSettingsError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise AgentSettingsError("profile_unavailable", "找不到有效的 AGH profile 配置。") from None

    default_account = config.get("defaultAccountId") if isinstance(config, dict) else None
    accounts = config.get("accounts", []) if isinstance(config, dict) else []
    active = [account for account in accounts if isinstance(account, dict)
              and account.get("enabled") is True
              and account.get("id") == "agnes-ai"
              and account.get("accountId") == default_account]
    if len(active) != 1:
        raise AgentSettingsError("profile_invalid", "当前 AGH profile 未选择可用的 Agnes account。")

    credential_ref = active[0].get("credentialRef")
    try:
        ref = urlsplit(credential_ref)
    except (TypeError, ValueError):
        raise AgentSettingsError("credential_invalid", "AGH Agnes 凭据引用无效。") from None
    filename = unquote(ref.path[1:]) if ref.path.startswith("/") else ""
    if (ref.scheme != "secret" or ref.netloc != "agnes-ai" or ref.query or ref.fragment
            or "/" in filename or "\\" in filename or not _CREDENTIAL_RE.fullmatch(filename)):
        raise AgentSettingsError("credential_invalid", "AGH Agnes 凭据引用无效。")

    secrets_candidate = home / "secrets" / "agnes-ai"
    secrets_dir = secrets_candidate.resolve(strict=False)
    if secrets_dir != secrets_candidate:
        raise AgentSettingsError("credential_invalid", "AGH Agnes 凭据目录不能是外部链接。")
    target_candidate = secrets_dir / filename
    if target_candidate.is_symlink():
        raise AgentSettingsError("credential_invalid", "AGH Agnes 凭据文件不能是链接。")
    target = target_candidate.resolve(strict=False)
    try:
        target.relative_to(secrets_dir)
    except ValueError:
        raise AgentSettingsError("credential_invalid", "AGH Agnes 凭据路径不在允许目录内。") from None
    return home, profile, secrets_dir, target


def get_agnes_key_status() -> dict:
    """Return only configured state and active profile name, never Key material."""
    _, profile, _, credential_path = _active_credential_path()
    try:
        if credential_path.stat().st_size > _MAX_SECRET_BYTES:
            return {"configured": False, "profile": profile}
        value = json.loads(credential_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"configured": False, "profile": profile}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"configured": False, "profile": profile}
    configured = (isinstance(value, dict)
                  and value.get("version") == 1
                  and value.get("kind") == "api-key"
                  and value.get("provider") == "agnes-ai"
                  and isinstance(value.get("value"), str)
                  and bool(value["value"].strip()))
    return {"configured": bool(configured), "profile": profile}


def _current_windows_user() -> str:
    try:
        result = subprocess.run(["whoami"], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=5, shell=False, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise AgentSettingsError("acl_failed", "无法确认当前 Windows 用户以设置凭据 ACL。") from None
    user = result.stdout.strip()
    if result.returncode != 0 or not user or "\n" in user or "\r" in user:
        raise AgentSettingsError("acl_failed", "无法确认当前 Windows 用户以设置凭据 ACL。")
    return user


def _run_acl_command(args: list[str]) -> None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=10, shell=False, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise AgentSettingsError("acl_failed", "AGH 凭据文件 ACL 设置失败。") from None
    if result.returncode != 0:
        raise AgentSettingsError("acl_failed", "AGH 凭据文件 ACL 设置失败。")


def _protect_private_file(path: Path) -> None:
    """Use grant-before-disable-inheritance, then re-grant current user + SYSTEM."""
    user = _current_windows_user()
    grants = [f"{user}:F", "NT AUTHORITY\\SYSTEM:F"]
    _run_acl_command(["icacls", str(path), "/grant", *grants])
    _run_acl_command(["icacls", str(path), "/inheritance:r"])
    _run_acl_command(["icacls", str(path), "/grant", *grants])


def save_agnes_api_key(api_key: str) -> None:
    """Atomically replace the active AGH secret only after the temp file is private."""
    if not isinstance(api_key, str):
        raise AgentSettingsError("key_invalid", "请输入有效的 Agnes API Key。")
    value = api_key.strip()
    if not value or len(value) > 8192 or any(ord(char) < 32 for char in value):
        raise AgentSettingsError("key_invalid", "请输入有效的 Agnes API Key。")

    _, _, secrets_dir, target = _active_credential_path()
    if not secrets_dir.is_dir():
        raise AgentSettingsError("credential_store_missing", "AGH Agnes 凭据目录不存在，请先初始化 AGH profile。")

    temp_name = None
    try:
        descriptor, temp_name = tempfile.mkstemp(prefix=".agnes-key-", suffix=".tmp", dir=str(secrets_dir))
        os.close(descriptor)
        temp_path = Path(temp_name)
        _protect_private_file(temp_path)
        payload = json.dumps({
            "version": 1, "kind": "api-key", "provider": "agnes-ai", "value": value,
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with temp_path.open("wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, target)
    except AgentSettingsError:
        raise
    except OSError:
        raise AgentSettingsError("credential_write_failed", "无法安全保存 Agnes API Key。") from None
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def require_local_same_origin(request) -> None:
    """Require a loopback peer and an explicit same-origin request to a loopback host."""
    client_host = getattr(getattr(request, "client", None), "host", None)
    try:
        if not client_host or not ipaddress.ip_address(client_host).is_loopback:
            raise ValueError("remote peer")
    except ValueError:
        raise AgentSettingsError("request_forbidden", "Key 设置仅允许本机同源请求。") from None

    origin = request.headers.get("origin")
    if not origin:
        raise AgentSettingsError("request_forbidden", "Key 设置仅允许本机同源请求。")
    try:
        origin_url = urlsplit(origin)
        request_url = request.url
        origin_host = origin_url.hostname or ""
        request_host = request_url.hostname or ""
        origin_port = origin_url.port or (443 if origin_url.scheme == "https" else 80)
        request_port = request_url.port or (443 if request_url.scheme == "https" else 80)
        origin_loopback = (origin_host.lower() == "localhost"
                           or ipaddress.ip_address(origin_host).is_loopback)
        request_loopback = (request_host.lower() == "localhost"
                            or ipaddress.ip_address(request_host).is_loopback)
        valid = (origin_url.scheme == request_url.scheme
                 and origin_host.lower() == request_host.lower()
                 and origin_port == request_port
                 and origin_loopback and request_loopback
                 and origin_url.username is None and origin_url.password is None)
    except ValueError:
        valid = False
    if not valid:
        raise AgentSettingsError("request_forbidden", "Key 设置仅允许本机同源请求。")
