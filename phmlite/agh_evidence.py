"""AGH 执行证据索引（P0 问题2）：run_id ↔ AGH 会话的精确定位、执行索引与双向对账。

背景：诊断证据有两半——业务半（phmlite 库：reports/tasks/run_log，带 run_id）与执行半
（AGH 会话事件流）。`agnes.mjs sessions list` 首行抓取会话的旧做法曾把无关的冒烟会话
打包成诊断证据（两半互相指认失败）。本模块以"事件数据中出现 run_id 字符串"为锚点
（诊断提示词与 report/task 的 curl 参数都强制携带 run_id），把会话窗口精确定位到 turn，
并产出可校验的执行索引：session_key、turn 范围、模型标识、逐条 tool/call（ID/时间/
命令摘要/状态）。

已知结构性事实：AGH 的 -p 会话按工作区复用（一个 session_key 内多 turn 递增），
因此"一次诊断一个 session_id"不成立，索引以 run_id 命中的 turn 为准。
"""
import json
import sqlite3
import time
from pathlib import Path


class EvidenceError(RuntimeError):
    """证据链无法建立或校验失败（导出必须拒绝而非降级打包）。"""


def load_events(agh_home, run_id=None, retries=2, delay=1.0):
    """读 sessions.db 事件。给 run_id 时只取数据中出现该 run_id 的行（锚点查询）；
    否则返回全量。daemon 落库有毫秒级延迟，run_id 首查未命中时短暂重试。"""
    db_path = Path(agh_home) / "data" / "sessions.db"
    if not db_path.exists():
        raise EvidenceError(f"AGH 会话库不存在: {db_path}")
    for attempt in range(1, retries + 1):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            if run_id:
                rows = conn.execute(
                    "SELECT ts, type, session_key, data FROM events "
                    "WHERE data LIKE ? ORDER BY rowid", (f"%{run_id}%",)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT ts, type, session_key, data FROM events ORDER BY rowid").fetchall()
        finally:
            conn.close()
        if rows or attempt == retries:
            return [dict(r) for r in rows]
        time.sleep(delay)
    return []


def _split_turns(events):
    """把会话事件按 turn/start..turn/end 切片；返回 [{turn, events:[...]}]。
    turn/start 之前的事件（真实 AGH 时序里 user/message 先于 turn/start）并入下一个 turn，
    turn/start 本身也计入 events——导出侧与索引必须共用这同一份切片，否则前导段里的
    原始 Prompt 会被漏掉（2026-10-11 P0：证据包无 user/message，索引却标 run_id_in_prompt）。"""
    turns, current, prelude = [], None, []
    for ev in events:
        if ev["type"] == "turn/start":
            current = {"turn": (json.loads(ev["data"]).get("turn")
                                if ev.get("data") else None),
                       "events": list(prelude) + [ev]}
            prelude = []
            turns.append(current)
            continue
        if ev["type"] == "turn/end":
            if current is not None:
                current["events"].append(ev)
            current = None
            prelude = []
            continue
        if current is not None:
            current["events"].append(ev)
        else:
            prelude.append(ev)
    return turns


def _hit(event, run_id):
    return run_id in (event.get("data") or "")


#: 写业务状态的接口：AGH 工具成功不代表它们成功（审查 P1 问题5）
WRITE_ENDPOINTS = ("/api/report/submit", "/api/task/create")


def build_index(events, run_id):
    """从事件流构建 run 的执行索引。run_id 未命中任何事件 → EvidenceError。"""
    session_keys = {ev.get("session_key") for ev in events if ev.get("session_key")}
    if not session_keys:
        raise EvidenceError(f"事件流中未找到 run_id={run_id} 的任何痕迹")
    if len(session_keys) > 1:
        raise EvidenceError(f"run_id={run_id} 命中多个会话: {sorted(session_keys)}")
    session_key = session_keys.pop()

    turns = _split_turns(events)
    hit_turns = [t for t in turns if any(_hit(ev, run_id) for ev in t["events"])]
    if not hit_turns:
        raise EvidenceError(f"run_id={run_id} 未命中任何 turn（会话 {session_key}）")

    window_events = [ev for t in hit_turns for ev in t["events"]]
    tool_calls, models, prompt_hits, arg_hits = [], set(), 0, 0
    results_by_id = {}
    for ev in window_events:
        if ev["type"] == "tool/result":
            try:
                data = json.loads(ev["data"] or "{}")
            except ValueError:
                continue
            results_by_id[data.get("toolUseId")] = bool(data.get("isError"))
    for ev in window_events:
        if ev["type"] == "user/message" and _hit(ev, run_id):
            prompt_hits += 1
        if ev["type"] != "tool/call":
            continue
        # 窗口内全部工具调用都入索引（取证类调用参数不含 run_id，同样是执行证据）
        if _hit(ev, run_id):
            arg_hits += 1
        try:
            data = json.loads(ev["data"] or "{}")
        except ValueError:
            continue
        args = data.get("args") or {}
        command = str(args.get("command", ""))
        # 审计匹配用摘要：长命令（如 PowerShell 包 UrlEnc+证据 JSON 再接 submit URL）保留头尾，
        # report/submit、task/create 等关键端点在尾部也不会丢
        if len(command) > 1600:
            command = command[:800] + "\n…(中间省略)…\n" + command[-800:]
        tool_calls.append({
            "tool_use_id": data.get("toolUseId"),
            "ts": ev["ts"],
            "name": data.get("name"),
            "command": command,
            "is_error": results_by_id.get(data.get("toolUseId")),
        })
    for ev in window_events:
        if ev["type"] == "cost/ledger":
            try:
                model = (json.loads(ev["data"] or "{}").get("model"))
            except ValueError:
                model = None
            if model:
                models.add(model)

    if not tool_calls:
        raise EvidenceError(f"run_id={run_id} 命中的 turn 内没有工具调用（会话 {session_key}）")
    # 审查 P1 问题5：AGH 工具成功 ≠ 业务操作成功。此前取证命令用 `curl -s`，HTTP 503 时 curl
    # 仍返回 0，工具层 `isError=false` 只能证明"工具没报错"，不能证明接口返回 200。
    # 现在的提示词要求取证命令带 `-f`（HTTP≥400 → curl 退出非零 → 工具报错），于是 is_error
    # 能真实反映调用失败；这里检查**每个写接口是否至少成功过一次**。
    #
    # 判据不能是"所有写调用都没错"：智能体第一次写被 400 拒绝、按提示修正后重试成功是
    # 正常行为，把失败的那次也算进来会把成功的运行判成未核验（2026-10-13 真机实测踩到）。
    write_calls = [c for c in tool_calls
                   if any(e in (c["command"] or "") for e in WRITE_ENDPOINTS)]
    per_endpoint = {
        endpoint: any(c["is_error"] is False and endpoint in (c["command"] or "")
                      for c in tool_calls)
        for endpoint in WRITE_ENDPOINTS
    }
    return {
        "run_id": run_id,
        "session_key": session_key,
        "turns": [t["turn"] for t in hit_turns],
        "window": {"first_ts": window_events[0]["ts"], "last_ts": window_events[-1]["ts"]},
        "model": sorted(models),
        "tool_calls": tool_calls,
        "checks": {
            "run_id_in_prompt": prompt_hits > 0,
            "run_id_in_tool_args": arg_hits > 0,
            "tool_call_count": len(tool_calls),
            "tool_error_count": sum(1 for c in tool_calls if c["is_error"]),
            "write_calls": len(write_calls),
            "write_calls_ok": all(per_endpoint.values()),
            "write_endpoints_ok": per_endpoint,
        },
    }


def index_for_run(agh_home, run_id, retries=2, delay=1.0):
    """定位 run 的会话并构建索引：先锚点查询，再取全会话做 turn 切片。"""
    hits = load_events(agh_home, run_id=run_id, retries=retries, delay=delay)
    if not hits:
        raise EvidenceError(f"AGH 会话库中找不到 run_id={run_id} 的痕迹")
    session_key = hits[0]["session_key"]
    conn = sqlite3.connect(str(Path(agh_home) / "data" / "sessions.db"))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT ts, type, session_key, data FROM events WHERE session_key=? ORDER BY rowid",
            (session_key,)).fetchall()
    finally:
        conn.close()
    return build_index([dict(r) for r in rows], run_id)


def session_window_events(agh_home, index):
    """按执行索引取回命中 turn 的事件原文（含 turn/start 之前的原始 Prompt），保持时序。

    证据包要能"独立核查"而不是"相信总结"，所以导出必须与 build_index 共用 _split_turns：
    P0（2026-10-11）就是导出侧自写了一份切片、漏掉 turn/start 前的前导段，
    导致 run_id_in_prompt=true 但包内看不到任何 user/message。"""
    session_key = (index or {}).get("session_key")
    if not session_key:
        raise EvidenceError("执行索引缺 session_key，无法导出会话事件")
    db_path = Path(agh_home) / "data" / "sessions.db"
    if not db_path.exists():
        raise EvidenceError(f"AGH 会话库不存在: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT ts, type, session_key, data FROM events WHERE session_key=? ORDER BY rowid",
            (session_key,)).fetchall()
    finally:
        conn.close()
    wanted = set(index.get("turns") or [])
    turns = _split_turns([dict(r) for r in rows])
    selected = [ev for t in turns if t["turn"] in wanted for ev in t["events"]]
    if not selected:
        raise EvidenceError(
            f"索引 turn {sorted(wanted)} 在会话 {session_key} 中取不到任何事件")
    return selected


def validate_linkage(index, reports, tasks):
    """业务半 ↔ 执行半双向对账；返回错误列表（空=通过）。
    reports/tasks 为该 run_id 的业务行，index 为执行索引。"""
    errors = []
    checks = index.get("checks") or {}
    if not checks.get("run_id_in_prompt"):
        errors.append("会话 prompt 未包含 run_id")
    if not checks.get("run_id_in_tool_args"):
        errors.append("会话工具参数未包含 run_id（业务↔执行断链）")
    commands = " ".join(tc.get("command", "") for tc in index.get("tool_calls", []))
    for rep in reports:
        if "api/report/submit" not in commands:
            errors.append(f"报告#{rep.get('id')} 在会话中无对应 report/submit 调用")
    for task in tasks:
        if "api/task/create" not in commands:
            errors.append(f"工单#{task.get('id')} 在会话中无对应 task/create 调用")
    n_submit = commands.count("api/report/submit")
    if reports and n_submit and n_submit < len(reports):
        errors.append(f"会话 report/submit 调用 {n_submit} 次 < 业务报告 {len(reports)} 条")
    return errors
