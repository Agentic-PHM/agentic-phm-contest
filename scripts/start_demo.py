# -*- coding: utf-8 -*-
"""评委/演示一条命令启动：AGH serve（如未启动）→ 暖机 → phmlite（如未启动）
→ 复位场景 → 打开浏览器。全部就绪后自动退出，两个服务保持后台运行。

用法：
  python scripts/start_demo.py            # 正常启动（会打开默认浏览器）
  python scripts/start_demo.py --no-browser
  python scripts/start_demo.py --no-warm  # 跳过模型暖机（省一次小调用）

前置：本机已完成一次性部署（pip install -r requirements.txt、AGH 运行时按
scripts/setup_agh.md 初始化）。AGH 未配置时监测/报警仍可用，自动诊断不可用（会明确提示）。
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

AGH_DIR = Path(os.environ.get("AGH_DIR", r"D:\agh-contest"))
AGH_HOME = Path(os.environ.get("AGH_HOME", str(AGH_DIR / "agh-home")))
PROFILE = os.environ.get("PHM_AGH_PROFILE", "local-dev")
AGH_PORT = os.environ.get("AGH_PORT", "4178")
LOG_DIR = REPO / "logs"
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def up(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def spawn(cmd, cwd, log_name):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_DIR / log_name, "ab")
    return subprocess.Popen(cmd, cwd=str(cwd), stdout=log, stderr=log,
                            creationflags=DETACHED, close_fds=True)


def wait_up(port, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if up(port):
            return True
        time.sleep(2)
    return False


def start_agh():
    if up(AGH_PORT):
        return "已在运行"
    if not (AGH_DIR / "node24" / "node.exe").is_file():
        return "未配置（AGH 运行时不存在）——监测/报警可用，自动诊断不可用"
    spawn([str(AGH_DIR / "node24" / "node.exe"), str(AGH_DIR / "runtime" / "agnes.mjs"),
           "serve", "--profile", PROFILE, "--cwd", str(REPO), "--port", AGH_PORT],
          cwd=AGH_DIR, log_name="agh-serve.log")
    ok = wait_up(AGH_PORT, 90)
    return "已启动" if ok else "启动超时（90s）——查看 logs/agh-serve.log"


def start_phmlite():
    if up(8181):
        return "已在运行"
    spawn([sys.executable, str(REPO / "scripts" / "run_server.py")],
          cwd=REPO, log_name="phmlite.log")
    return "已启动" if wait_up(8181, 30) else "启动失败——查看 logs/phmlite.log"


def warm_model():
    """一次最小 -p 调用：验证 daemon/会话/模型全链路，并规避首跑竞态。"""
    from phmlite.agh_runtime import run_prompt
    out = run_prompt("只回复两个字母：ok", timeout=120)
    return "通过" if out.strip() else "空回复（Agnes 网关限流，稍后自动诊断会自动重试）"


def reset_scenario():
    """演示起点干净：故障复位为 none（需要控制台凭据，登录换 token）。"""
    import json
    req = urllib.request.Request(
        "http://127.0.0.1:8181/api/login",
        data=json.dumps({"username": "admin", "password": "123456"}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        token = json.loads(r.read())["token"]
    for path in ("/api/scenario/set?key=fault.type&value=none",
                 "/api/scenario/set?key=fault.severity&value=0"):
        req = urllib.request.Request("http://127.0.0.1:8181" + path,
                                     headers={"X-Operator-Token": token})
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-warm", action="store_true")
    args = ap.parse_args()

    print("== Agentic-PHM 演示启动 ==")
    agh_state = start_agh()
    print(f"[1/4] AGH 运行时(4178)：{agh_state}")
    phm_state = start_phmlite()
    print(f"[2/4] 平台后端(8181)：{phm_state}")
    if not up(8181):
        print("后端未能启动，终止。"); sys.exit(1)

    warm = "跳过"
    if agh_state in ("已在运行", "已启动") and not args.no_warm:
        try:
            warm = warm_model()
        except Exception as exc:  # noqa: BLE001 —— 暖机失败不阻塞启动
            warm = f"未通过（{type(exc).__name__}）：自动诊断会自动重试；监测/报警不受影响"
    print(f"[3/4] 模型链路暖机：{warm}")

    try:
        reset_scenario()
        print("[4/4] 演示场景已复位（fault=none）")
    except Exception:  # noqa: BLE001
        print("[4/4] 场景复位失败（可登录后在实时监视页手动恢复正常）")

    url = f"http://127.0.0.1:8181/?v={int(time.time())}"
    if not args.no_browser:
        webbrowser.open(url)
    print(f"\n演示地址：{url}（登录 admin / 123456）")
    if "未配置" in agh_state:
        print("提示：AGH 运行时未配置，自动诊断不可用；完整演示需按 scripts/setup_agh.md 初始化。")
    print("两个服务已在后台运行（日志在 logs/）；停止演示可运行 python scripts/stop_demo.py。")


if __name__ == "__main__":
    main()
