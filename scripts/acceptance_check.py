# -*- coding: utf-8 -*-
"""真机验收（P1：自动化测试未覆盖真实模型链路）——演示前的三步在线验收门。

与 pytest 套件互补：pytest 全部离线（注入模拟执行器，保证可重复）；
本脚本跑【真实链路】，任何一步失败即 FAIL（exit 1），并给出定位提示。
演示日前必跑一次，通过后再进演示流程。

用法：D:/Python311/python.exe scripts/acceptance_check.py [--skip-e2e]
前置：phmlite(8181) 与 AGH serve(4178) 已启动；Agnes Key 已配置且网关未限流。
"""
import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from phmlite.agh_runtime import probe_provider, run_prompt  # noqa: E402
from phmlite import operator_auth  # noqa: E402
from blind_benchmark import BASE, api, login as login_operator, run_e2e_case  # noqa: E402

RESULTS = []
#: 控制面凭据：登录后填充（P1 问题4——不再用固定字符串 X-Operator: console）
OP = {}


def step(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-e2e", action="store_true", help="只验模型链路，不跑完整盲诊用例")
    args = ap.parse_args()
    # P1 问题4：控制面凭据必须填进 blind_benchmark 的 OP（run_e2e_case 用的是它）
    OP.update(login_operator())

    # 第 0 步：phmlite 可达（连不上就别跑后面，报清晰定位）
    try:
        api("/api/autodiag/status", OP)
        step("⓪ phmlite 服务可达（8181）", True)
    except Exception:  # noqa: BLE001
        step("⓪ phmlite 服务可达（8181）", False,
             "python scripts/run_server.py 未启动或已退出")
        return finish()

    # 复位到干净状态
    api("/api/scenario/set?key=fault.type&value=none", OP)
    api("/api/scenario/set?key=fault.severity&value=0", OP)

    # 第 1 步：Agnes 供应商标识（key/模型/profile 经 AGH doctor 全量校验）
    try:
        probe_provider(timeout=60)
        step("① Agnes 供应商探测（key/模型/profile）", True)
    except Exception as exc:  # noqa: BLE001
        step("① Agnes 供应商探测", False,
             f"{getattr(exc, 'category', '')}：检查系统设置页 Key，或 Agnes 网关限流（隔几分钟重试）")
        return finish()

    # 第 2 步：最小 -p 会话（daemon+会话+yolo+模型全链路）
    try:
        out = run_prompt("只回复两个字母：ok", timeout=120)
        step("② AGH 最小会话（daemon/会话/yolo/模型）", out.strip() == "ok",
             "空回复或非预期输出：重启 AGH serve 后重试（详见交接文档坑 15）" if out.strip() != "ok" else "")
    except Exception as exc:  # noqa: BLE001
        step("② AGH 最小会话", False, str(exc)[:150])
        return finish()

    if args.skip_e2e:
        return finish()

    # 第 3 步：一条真实盲诊用例（隐藏真值，全链路：报警→唤起→取证→报告→工单）
    case = {"id": "acceptance-bpfo", "truth": "bpfo", "mode": "e2e",
            "fault": "bpfo", "severity": 0.8}
    r = run_e2e_case(case)
    ok = (r["said"] == "bpfo" and r["report_id"] and r["task_created"])
    detail = (f"判定 {r['said']}（真值 bpfo）、延迟 {r['latency_s']}s、"
              f"报告#{r['report_id']}、工单{'已生成' if r['task_created'] else '缺失'}"
              + (f"、失败原因 {r['failure_reason']}" if r.get("failure_reason") else ""))
    step("③ 真实盲诊用例（bpfo/0.8 全链路）", ok, detail)

    # 收尾：恢复干净状态
    api("/api/scenario/set?key=fault.type&value=none", OP)
    api("/api/scenario/set?key=fault.severity&value=0", OP)
    finish()


def finish():
    all_ok = all(ok for _, ok, _ in RESULTS)
    print("\n验收结论：" + ("PASS——真实模型链路可用，可以进入演示。" if all_ok else "FAIL——见上方定位提示。"))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
