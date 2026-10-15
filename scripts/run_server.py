"""启动入口：uvicorn phm-lite（默认 127.0.0.1:8181）。

P1 问题6：本应用是**本机演示**——控制台账号是演示账号、业务路由没有完整会话鉴权与逐路由权限、
写操作仍是 GET。因此绑定非回环地址需要显式确认；否则一个可写演示系统会被"顺手绑 0.0.0.0"
暴露出去。对外提供评审链接前，先按 README「对外暴露前必须补齐」清单整改。
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 允许从任意 CWD 启动

import uvicorn

LOOPBACK = ("127.0.0.1", "::1", "localhost")
EXPOSURE_CHECKLIST = """对外暴露前必须补齐（README「对外暴露前必须补齐」）：
  1. 服务端身份认证：业务路由校验会话，而不是只有控制面；
  2. 逐路由权限：报告/工单/场景配置按角色授权；
  3. CSRF 安全方法：修改数据的 GET 接口改为 POST/PATCH；
  4. 操作审计留存：谁在何时改了什么，可追溯；
  5. TLS 终结与访问控制（反向代理），并轮换全部演示凭据。

注意两个开关是分开的：
  绑定非回环地址  -> PHM_ALLOW_NON_LOOPBACK=1      （本脚本把关，可配合本机反向代理）
  放行远端客户端  -> PHM_ALLOW_REMOTE_CLIENTS=1    （服务端中间件把关，默认拒绝）"""


def resolve_bind(host, port):
    """返回 (host, port)；绑定非回环地址且未显式确认时抛出 SystemExit。"""
    if host not in LOOPBACK and os.environ.get("PHM_ALLOW_NON_LOOPBACK") != "1":
        raise SystemExit(
            f"拒绝绑定非回环地址 {host}：{EXPOSURE_CHECKLIST}\n"
            f"确需对外暴露，请显式设置 PHM_ALLOW_NON_LOOPBACK=1 后重试。")
    return host, int(port)


def main(argv=None):
    ap = argparse.ArgumentParser(description="启动 phm-lite（默认仅本机回环）")
    ap.add_argument("--host", default="127.0.0.1", help="绑定地址（默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=8181, help="绑定端口（默认 8181）")
    args = ap.parse_args(argv)
    host, port = resolve_bind(args.host, args.port)
    uvicorn.run("phmlite.server:app", host=host, port=port)


if __name__ == "__main__":
    main()
