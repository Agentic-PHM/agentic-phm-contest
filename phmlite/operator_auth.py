"""控制面客户端凭据（P1 问题4）：脚本与测试用它登录换取控制台会话 token。

服务端控制面只认登录签发的随机会话 token（见 `server._require_operator`）。此前所有脚本和
前端都用固定字符串 `X-Operator: console`——那个值同样打在前端构建产物里，任何能发 HTTP 的人
（包括诊断智能体加一个 `-H`）都能伪造，既不是凭据也谈不上边界。

账号可用 `PHM_OPERATOR_USER` / `PHM_OPERATOR_PASSWORD` 覆盖；**盲测/演示环境应设置非公开口令**，
公开的 admin/123456 只算演示登录，不构成授权边界。
"""
import json
import os
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8181"


def login(base_url=DEFAULT_BASE, user=None, password=None, timeout=10):
    """登录并返回控制面请求头 dict（`X-Operator-Token`）。"""
    payload = {
        "username": user or os.environ.get("PHM_OPERATOR_USER", "admin"),
        "password": password or os.environ.get("PHM_OPERATOR_PASSWORD", "123456"),
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/login",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {"X-Operator-Token": json.loads(resp.read())["token"]}
