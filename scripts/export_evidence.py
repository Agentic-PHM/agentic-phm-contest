"""证据打包（P0 问题2 重写）：按 run_id 精确定位 AGH 会话，双向对账校验后才导出。

用法：python scripts/export_evidence.py --run-id auto-1011-031333-29051e69

与旧版的差别：不再取 `sessions list` 首行（曾把无关冒烟会话打包成诊断证据），
改为用 run_id 作锚点（提示词与 report/task 的 curl 参数都强制携带 run_id）在
AGH sessions.db 中定位命中的 turn 窗口，产出执行索引（session_key/turn/模型/
逐条 tool_use_id/时间/命令摘要/状态），并对业务半（reports/tasks/run_log）做
双向对账；任一关联校验失败即拒绝导出（exit 1），绝不静默降级。
"""
import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 允许从任意 CWD 启动

from phmlite import agh_evidence  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def collect_db(db_path: Path, run_id: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    run_log = [dict(r) for r in conn.execute(
        "SELECT ts, run_id, tool, args, latency_ms FROM run_log WHERE run_id=? OR "
        "(tool IN ('/api/report/submit','/api/task/create') AND args LIKE ?) ORDER BY id",
        (run_id, f"%{run_id}%"))]
    reports = [dict(r) for r in conn.execute(
        "SELECT * FROM reports WHERE run_id=? ORDER BY id", (run_id,))]
    tasks = [dict(r) for r in conn.execute(
        "SELECT * FROM tasks WHERE run_id=? ORDER BY id", (run_id,))]
    alarms = [dict(r) for r in conn.execute(
        "SELECT * FROM alarms WHERE equip='P-101' ORDER BY id")]
    conn.close()
    return {"run_log": run_log, "reports": reports, "tasks": tasks, "alarms": alarms}


def write_session_events(agh_home: Path, index: dict, out_dir: Path) -> list:
    """导出命中 turn 的事件原文（含 turn/start 之前的原始 Prompt），写 agh_session_events.jsonl。

    切片委托 agh_evidence.session_window_events，与执行索引共用同一套逻辑：本文件曾自写一份
    切片并漏掉 turn/start 前的前导段，结果索引标 run_id_in_prompt=true 而包内没有 Prompt
    （2026-10-11 P0，评委无法独立核查"智能体没见过注入真值"）。"""
    kept = agh_evidence.session_window_events(agh_home, index)
    with (out_dir / "agh_session_events.jsonl").open("w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--db", default=str(REPO / "data" / "phmlite.db"))
    ap.add_argument("--agh-home", default=r"D:\agh-contest\agh-home")
    ap.add_argument("--out", default=str(REPO / "evidence"))
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) / f"run_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    agh_home = Path(args.agh_home)

    # 1) 执行索引：优先用诊断落盘的 <run_id>.index.json，否则按 run_id 现场定位
    index_path = Path(args.db).parent / "autodiag" / f"{args.run_id}.index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index_source = str(index_path)
    else:
        index = agh_evidence.index_for_run(agh_home, args.run_id)
        index_source = "live-locate"
        (out_dir / "agh_evidence_index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    # 2) 业务半
    bundle = {"run_id": args.run_id, "exported_at": datetime.now().isoformat(timespec="seconds"),
              "index_source": index_source}
    bundle.update(collect_db(Path(args.db), args.run_id))

    # 3) 双向对账：业务半 ↔ 执行半，任一失败拒绝导出
    linkage_errors = agh_evidence.validate_linkage(index, bundle["reports"], bundle["tasks"])
    if len(bundle["reports"]) < 1:
        linkage_errors.append(f"业务库中 run_id={args.run_id} 没有报告")
    if not index["model"]:
        linkage_errors.append("执行索引缺模型标识")
    if index["checks"]["tool_call_count"] < 5:
        linkage_errors.append(f"工具调用仅 {index['checks']['tool_call_count']} 次（完整诊断应≥5）")

    # 4) 导出：执行索引 + 命中 turn 的事件原文 + checkpoints/audit 原件
    (out_dir / "agh_evidence_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    exported_events = write_session_events(agh_home, index, out_dir)
    event_count = len(exported_events)
    # P0（2026-10-11）：索引断言"run_id 在 prompt 中"，证据包就必须真的能看到那条 prompt，
    # 否则评委只能相信总结文件。缺 prompt 一律拒绝导出。
    prompt_in_export = any(
        e.get("type") == "user/message" and args.run_id in (e.get("data") or "")
        for e in exported_events)
    if not prompt_in_export:
        linkage_errors.append(
            "导出事件缺 run_id 命中的原始 Prompt（user/message），无法独立核查盲诊口径")
    ckpt = agh_home / "data" / "checkpoints" / index["session_key"].split(":")[-1]
    if ckpt.exists():
        shutil.copytree(ckpt, out_dir / "agh_checkpoint", dirs_exist_ok=True)
    audit = agh_home / "data" / "audit"
    if audit.exists():
        shutil.copytree(audit, out_dir / "agh_audit", dirs_exist_ok=True)

    bundle["agh"] = {"session_key": index["session_key"], "turns": index["turns"],
                     "model": index["model"], "tool_calls": index["tool_calls"],
                     "checks": index["checks"], "event_count": event_count,
                     "note": "命中 turn 的完整事件（含 turn/start 之前的原始 Prompt）；完整会话以 AGH 审计为准"}
    (out_dir / "bundle.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=1), encoding="utf-8")

    checks = {
        "索引含 run_id 于 prompt": index["checks"]["run_id_in_prompt"],
        "索引含 run_id 于工具参数": index["checks"]["run_id_in_tool_args"],
        "工具调用 ≥5": index["checks"]["tool_call_count"] >= 5,
        "模型标识已记录": bool(index["model"]),
        "报告 =1": len(bundle["reports"]) == 1,
        "run_log 留痕 ≥2（report+task）": len(bundle["run_log"]) >= 2,
        "双向对账通过": not linkage_errors,
        "会话事件已导出": event_count > 0,
        "包内含原始 Prompt（可独立核查）": prompt_in_export,
    }
    print(json.dumps({k: ("PASS" if v else "FAIL") for k, v in checks.items()},
                     ensure_ascii=False, indent=1))
    if linkage_errors:
        print("关联校验失败：")
        for e in linkage_errors:
            print(" -", e)
    print("evidence dir:", out_dir)
    if not all(checks.values()):
        # 关联校验失败也要留下 bundle 供排查，但退出码非 0——绝不静默出"合格"证据
        raise SystemExit(1)


if __name__ == "__main__":
    main()
