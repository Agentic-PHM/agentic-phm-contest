# -*- coding: utf-8 -*-
"""独立评分器（P0 防真值泄漏的配套证据工具）。

对一次自动诊断 run 做三项独立核查，产出可留档的 JSON 判定：
  1. prompt_audit   —— 诊断提示词原文（data/autodiag/<run_id>.log 的 PROMPT 段）不得含注入真值
                       （fault.type/severity/BPFO/BPFI/外圈/内圈字样）；
  2. truth_match    —— 报告结论与 scenario.json 真值比对（盲诊正确性）；
  3. audit_scan     —— 尽力扫描 AGH 审计目录中会话时间窗内的 skill/会话文件是否出现
                       "scenario" 字样（深核查以 AGH 工作台审计为准，此处为初筛）。

用法：
  D:/Python311/python.exe scripts/score_autodiag.py [--run-id auto-xxxx] [--out evidence/xxx.json]
不指定 --run-id 时取最新一条 auto-* 报告。
"""
import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRUTH_WORDS = ("bpfo", "bpfi", "外圈", "内圈", "fault.type", "severity")
LABEL_MAP = {"bpfo": "BPFO", "bpfi": "BPFI", "none": "误报"}


def load_report(db_path, run_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if run_id:
        row = conn.execute("SELECT * FROM reports WHERE run_id=? ORDER BY id DESC", (run_id,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM reports WHERE run_id LIKE 'auto-%' ORDER BY id DESC").fetchone()
    conn.close()
    return dict(row) if row else None


def check_prompt_audit(log_path):
    """提示词不得含真值字样；返回 (ok, 命中列表)。"""
    if not log_path.exists():
        return False, [f"提示词日志缺失: {log_path}"]
    text = log_path.read_text(encoding="utf-8", errors="replace")
    head = text.split("PROMPT:", 1)[1].split("\n--", 1)[0] if "PROMPT:" in text else text
    hits = [w for w in TRUTH_WORDS if w in head.lower() or w in head]
    return (not hits), hits


def check_truth_match(report, truth_type):
    """报告结论/部件 与 注入真值 比对。"""
    conclusion = str(report.get("conclusion", ""))
    said = None
    for key, label in LABEL_MAP.items():
        if key != "none" and label.lower() in conclusion.lower():
            said = key
    if said is None and "误报" in conclusion:
        said = "none"
    expected_component = "bearing_de" if truth_type in ("bpfo", "bpfi") else None
    comp_ok = report.get("component") == expected_component if expected_component else True
    return said == truth_type, {"truth": truth_type, "said": said,
                                "component": report.get("component"),
                                "component_ok": comp_ok,
                                "confidence": report.get("confidence")}


def check_agh_audit(aghaudit_dir, started_at, finished_at):
    """尽力初筛：时间窗内改动的审计文件里是否出现 scenario 字样。找不到窗内文件则返回 unknown。"""
    if not Path(aghaudit_dir).exists():
        return "unknown", f"AGH 审计目录不存在: {aghaudit_dir}"
    fmt = "%Y-%m-%dT%H:%M:%S"
    t0 = time.mktime(time.strptime(started_at, fmt))
    t1 = time.mktime(time.strptime(finished_at, fmt)) if finished_at else time.time()
    hits, scanned = [], 0
    for p in Path(aghaudit_dir).rglob("*"):
        if p.is_file() and t0 - 5 <= p.stat().st_mtime <= t1 + 5:
            scanned += 1
            try:
                if "scenario" in p.read_text(encoding="utf-8", errors="ignore").lower():
                    hits.append(p.name)
            except OSError:
                pass
    if scanned == 0:
        return "unknown", "会话时间窗内未找到可扫的审计文件（以 AGH 工作台审计为准）"
    return ("fail" if hits else "pass"), {"scanned": scanned, "hits": hits}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--db", default=str(REPO / "data" / "phmlite.db"))
    ap.add_argument("--scenario", default=str(REPO / "data" / "scenario.json"))
    ap.add_argument("--log-dir", default=str(REPO / "data" / "autodiag"))
    ap.add_argument("--agh-audit", default=None, help="AGH 审计目录（默认 AGH_HOME/data/audit）")
    ap.add_argument("--started-at", default=None, help="ISO 时间；缺省用报告 ts 往前推 10 分钟")
    ap.add_argument("--finished-at", default=None)
    ap.add_argument("--out", default=None, help="判定 JSON 落盘路径")
    args = ap.parse_args()

    report = load_report(args.db, args.run_id)
    if not report:
        print("找不到 auto-* 报告（先跑一轮注入诊断）"); sys.exit(2)
    run_id = report["run_id"]
    truth = (json.loads(Path(args.scenario).read_text(encoding="utf-8")).get("fault") or {}).get("type")
    prompt_ok, prompt_hits = check_prompt_audit(Path(args.log_dir) / f"{run_id}.log")
    truth_ok, detail = check_truth_match(report, truth)
    started = args.started_at or (datetime.fromisoformat(report["ts"]) - timedelta(minutes=10)).isoformat(timespec="seconds")
    finished = args.finished_at
    audit_state, audit_detail = "skip", "未提供 AGH 审计目录"
    if args.agh_audit:
        audit_state, audit_detail = check_agh_audit(args.agh_audit, started, finished or started)

    verdict = {
        "run_id": run_id, "report_id": report["id"], "ts": report["ts"],
        "prompt_audit": {"ok": prompt_ok, "hits": prompt_hits},
        "truth_match": {"ok": truth_ok and detail.get("component_ok", True), "detail": detail},
        "agh_audit_scan": {"state": audit_state, "detail": audit_detail},
        "verdict": "PASS" if (prompt_ok and truth_ok and detail.get("component_ok", True)
                              and audit_state in ("pass", "skip", "unknown")) else "FAIL",
    }
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已写出", args.out)
    sys.exit(0 if verdict["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
