# -*- coding: utf-8 -*-
"""任务② E2E 驱动：注入 bpfo/0.8 → 后台轮询观察 → 记录状态机与耗时。零人工介入。

P0 问题3：采样由后端独立循环驱动，本脚本的 /api/device 轮询只用于观察，不再推进报警判定。"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phmlite import operator_auth  # noqa: E402

BASE = "http://127.0.0.1:8181"
#: 控制面凭据（P1 问题4）：scenario 写接口需登录签发的会话 token
OP = operator_auth.login(BASE)


def get(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


# t0 注入（演示场景的唯一人工动作）
get("/api/scenario/set?key=fault.type&value=bpfo", OP)
get("/api/scenario/set?key=fault.severity&value=0.8", OP)
t0 = time.time()
print(f"[{time.strftime('%H:%M:%S')}] t0 注入 bpfo severity=0.8")

last_state, alarm_ts, done_ts = None, None, None
deadline = t0 + 180
log = []
while time.time() < deadline:
    try:  # 只读观察（采样/报警由后端采样循环推进，P0 问题3）
        get("/api/device?run_id=auto-e2e")
    except Exception as e:
        print("device poll err", e)
    st = get("/api/autodiag/status")
    if st["state"] != last_state:
        last_state = st["state"]
        print(f"[{time.strftime('%H:%M:%S')}] +{time.time()-t0:5.1f}s state -> {last_state} mode={st['mode']}")
    if st["alarm"] and not alarm_ts:
        alarm_ts = st["alarm"]["ts"]
        print(f"[{time.strftime('%H:%M:%S')}] +{time.time()-t0:5.1f}s severe alarm {st['alarm']}")
    if st["state"] in ("done", "fallback", "failed"):
        done_ts = time.time()
        break
    time.sleep(4)

print("\n==== events ====")
for e in st["events"]:
    print(f"  {e['ts']} {e['text']}")
print("\n==== 结论 ====")
print("state:", st["state"], "mode:", st["mode"], "run_id:", st["run_id"])
print("report_id:", st["report_id"], "task_id:", st["task_id"])
if st["report_id"]:
    rep = [r for r in get("/api/report/list?equip=P-101")["rows"] if r["id"] == st["report_id"]][0]
    print("conclusion:", rep["conclusion"])
    print("confidence:", rep["confidence"], "component:", rep["component"])
    print("evidence steps:", len(json.loads(rep["evidence"])) if rep["evidence"].startswith("[") else "?")
total = done_ts - t0 if done_ts else None
print(f"\n注入→终态耗时: {total:.1f}s (验收 ≤90s)" if total else "未在预算内收敛！")
