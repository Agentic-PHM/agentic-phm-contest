"""P1 问题6 回归：本机 Demo 边界与启动绑定守卫。

本应用是离线演示（固定演示账号、业务路由没有完整会话鉴权、写操作仍是 GET）。因此：
 - 服务端硬性拒绝非回环来源，避免"顺手绑 0.0.0.0"就变成公网可写；
 - 启动脚本绑定非回环地址需要显式确认；
 - 关单这类控制面操作要求会话凭据。
"""
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import api, operator_headers
from test_autodiag import ad_db  # noqa: F401  复用会话级库夹具
from phmlite.server import LOOPBACK_HOSTS, _allow_request, _is_loopback_request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_server import EXPOSURE_CHECKLIST, resolve_bind  # noqa: E402


class _Client:
    def __init__(self, host):
        self.host = host


class _Request:
    def __init__(self, host):
        self.client = None if host is None else _Client(host)


def test_loopback_predicate_accepts_local_sources():
    for host in ("127.0.0.1", "::1", "localhost", "testclient", ""):
        assert _is_loopback_request(_Request(host)) is True, host
    assert _is_loopback_request(_Request(None)) is True   # 无 client 信息按本机处理
    assert "127.0.0.1" in LOOPBACK_HOSTS


def test_loopback_predicate_rejects_everything_else():
    for host in ("0.0.0.0", "8.8.8.8", "192.168.1.10", "10.0.0.7", "172.16.5.4"):
        assert _is_loopback_request(_Request(host)) is False, host


def test_bind_flag_does_not_by_itself_open_the_gate(monkeypatch):
    """两个开关必须分开：为了"能绑 0.0.0.0"而设的绑定开关，不得顺手放行远端客户端。

    实测教训：合成一个开关时，为了绑 0.0.0.0 就得关掉请求守卫——守卫等于没有，
    局域网 IP 直连拿到了 200（已由 uvicorn 访问日志证实）。"""
    monkeypatch.setenv("PHM_ALLOW_NON_LOOPBACK", "1")     # 允许绑定
    monkeypatch.delenv("PHM_ALLOW_REMOTE_CLIENTS", raising=False)
    assert _allow_request(_Request("172.29.240.1")) is False   # 远端仍被拒
    assert _allow_request(_Request("127.0.0.1")) is True

    monkeypatch.setenv("PHM_ALLOW_REMOTE_CLIENTS", "1")   # 独立开关才放行远端
    assert _allow_request(_Request("172.29.240.1")) is True


def test_startup_refuses_non_loopback_bind_without_optin(monkeypatch):
    monkeypatch.delenv("PHM_ALLOW_NON_LOOPBACK", raising=False)
    with pytest.raises(SystemExit) as exc:
        resolve_bind("0.0.0.0", 8181)
    message = str(exc.value)
    assert "对外暴露前必须补齐" in message
    assert "PHM_ALLOW_NON_LOOPBACK=1" in message
    assert "改为 POST/PATCH" in EXPOSURE_CHECKLIST


def test_startup_allows_loopback_and_explicit_optin(monkeypatch):
    assert resolve_bind("127.0.0.1", 8181) == ("127.0.0.1", 8181)
    assert resolve_bind("localhost", 9000) == ("localhost", 9000)
    monkeypatch.setenv("PHM_ALLOW_NON_LOOPBACK", "1")
    assert resolve_bind("0.0.0.0", 8181) == ("0.0.0.0", 8181)


def test_task_close_is_a_control_plane_action(ad_db, base_url):
    """关单要么持控制台凭据，要么 403——它不在诊断智能体的调用面上。"""
    tid = ad_db.create_task("检查", "依据", "severe", "manual")
    req = urllib.request.Request(base_url + f"/api/task/close?id={tid}&note=x")
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("无凭据关单应被拒绝")
    except urllib.error.HTTPError as e:
        assert e.code == 403
    assert ad_db.get_task(tid)["status"] == "open"   # 被拒后工单未被改动
    assert api(base_url, f"/api/task/close?id={tid}&note=x",
               operator_headers(base_url))["status"] == "closed"
