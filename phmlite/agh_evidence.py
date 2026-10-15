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
import re
import shlex
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs


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

def request_details(command):
    """Recognize a single local curl request, never an endpoint mentioned by echo."""
    command = (command or "").strip()
    # F6：PowerShell 反引号续行——把"行尾反引号(+空白)+换行"折叠成空格；
    # 其它位置的反引号保留，随后按命令替换拒绝。
    command = re.sub(r"`[ \t]*\r?\n[ \t]*", " ", command)
    bindings = {}
    literal_binding = re.compile(r"\A[ \t]*\$env:([A-Za-z_]\w*)[ \t]*=[ \t]*'((?:[^']|'')*)'[ \t]*(?:;[ \t]*|\r?\n)")
    while command.startswith("$env:"):
        match = literal_binding.match(command)
        if not match:
            return None, None
        bindings[match[1].upper()] = match[2].replace("''", "'")
        command = command[match.end():].lstrip()
    quote, escaped = None, False
    for ch in command:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote is not None:
            escaped = True
            continue
        if ch in ('"', "'"):
            if quote == ch:
                quote = None
            elif quote is None:
                quote = ch
        elif quote is None and ch in "&;|<>\r\n":
            return None, None
    if quote:
        return None, None
    # F3：开头的 $env: 字面量绑定已消费；参数值里对已绑定变量的引用解析为字面量后，
    # 任何残留的 $（未绑定引用/命令替换）或反引号都不是字面量命令，不作为有效取证调用。
    def _resolve(match):
        name = match.group(1).upper()
        if name not in bindings:
            raise ValueError(f"unbound $env:{name}")
        return bindings[name]

    try:
        command = re.sub(r"\$env:([A-Za-z_]\w*)", _resolve, command)
    except ValueError:
        return None, None
    if re.search(r"[$`]", command):
        return None, None
    if not re.match(r'^(?:curl(?:\.exe)?|C:\\Windows\\System32\\curl\.exe)\s', command, re.I):
        return None, None
    try:
        tokens = shlex.split(command, posix=False)[1:]
    except ValueError:
        return None, None
    urls, encoded, i = [], [], 0
    flags = {"-sS", "-s", "-f", "-G", "--get", "--fail-with-body", "--globoff"}
    while i < len(tokens):
        token = tokens[i].strip('"\'')
        if token in flags:
            i += 1
        elif token == "--data-urlencode" and i + 1 < len(tokens):
            value = tokens[i + 1].strip('"\'')
            if "=" not in value:
                return None, None
            encoded.append(value.split("=", 1))
            i += 2
        elif token.startswith(("http://", "https://")):
            urls.append(token)
            i += 1
        else:
            return None, None
    if len(urls) != 1:
        return None, None
    url = urlsplit(urls[0])
    if url.hostname not in ("127.0.0.1", "localhost"):
        return None, None
    args = {k: v[-1] for k, v in parse_qs(url.query).items()}
    for key, value in encoded:
        if "$env:" in value:
            reference = re.fullmatch(r"\$env:([A-Za-z_]\w*)", value)
            if not reference or reference[1].upper() not in bindings:
                return None, None
            value = bindings[reference[1].upper()]
        args[key] = value
    return url.path, args

def response_json(data):
    texts = [c.get("text", "") for c in data.get("content", []) if c.get("type") == "text"]
    for text in texts:
        try:
            value, _ = json.JSONDecoder().raw_decode(text.lstrip())
            if isinstance(value, dict):
                return value
        except (ValueError, TypeError):
            pass
    return None


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

    attempt_turns = [t["turn"] for t in hit_turns]
    hit_turns = hit_turns[-1:]

    window_events = [ev for t in hit_turns for ev in t["events"]]
    tool_calls, models, prompt_hits, arg_hits = [], set(), 0, 0
    results_by_id = {}
    for ev in window_events:
        if ev["type"] == "tool/result":
            try:
                data = json.loads(ev["data"] or "{}")
            except ValueError:
                continue
            results_by_id[data.get("toolUseId")] = data
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
        endpoint, request_args = request_details(command)
        result = results_by_id.get(data.get("toolUseId"), {})
        # 审计匹配用摘要：长命令（如 PowerShell 包 UrlEnc+证据 JSON 再接 submit URL）保留头尾，
        # report/submit、task/create 等关键端点在尾部也不会丢
        if len(command) > 1600:
            command = command[:800] + "\n…(中间省略)…\n" + command[-800:]
        tool_calls.append({
            "tool_use_id": data.get("toolUseId"),
            "ts": ev["ts"],
            "name": data.get("name"),
            "command": command,
            "is_error": result.get("isError") if isinstance(result.get("isError"), bool) else None,
            "endpoint": endpoint, "request_args": request_args,
            "response": response_json(result),
        })
    for ev in window_events:
        if ev["type"] == "cost/ledger":
            try:
                model = (json.loads(ev["data"] or "{}").get("model"))
            except ValueError:
                model = None
            if model:
                models.add(model)

    # 审查 P1 问题5：AGH 工具成功 ≠ 业务操作成功。此前取证命令用 `curl -s`，HTTP 503 时 curl
    # 仍返回 0，工具层 `isError=false` 只能证明"工具没报错"，不能证明接口返回 200。
    # 现在的提示词要求取证命令带 `-f`（HTTP≥400 → curl 退出非零 → 工具报错），于是 is_error
    # 能真实反映调用失败；这里检查**每个写接口是否至少成功过一次**。
    #
    # 判据不能是"所有写调用都没错"：智能体第一次写被 400 拒绝、按提示修正后重试成功是
    # 正常行为，把失败的那次也算进来会把成功的运行判成未核验（2026-10-13 真机实测踩到）。
    write_calls = [c for c in tool_calls if c["endpoint"] in WRITE_ENDPOINTS]
    per_endpoint = {
        endpoint: any(c["is_error"] is False and c["endpoint"] == endpoint
                      and (c.get("request_args") or {}).get("run_id") == run_id
                      and (c.get("response") or {}).get("run_id") == run_id
                      and isinstance((c.get("response") or {}).get("id"), int)
                      for c in tool_calls)
        for endpoint in WRITE_ENDPOINTS
    }
    return {
        "run_id": run_id,
        "session_key": session_key,
        "turns": [t["turn"] for t in hit_turns],
        "attempt_turns": attempt_turns,
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
            "turn_completed": all(any(ev["type"] == "turn/end" and
                                      json.loads(ev["data"] or "{}").get("reason") == "completed"
                                      for ev in t["events"]) for t in hit_turns),
        },
    }


def index_for_run(agh_home, run_id, retries=2, delay=1.0):
    """定位 run 的会话并构建索引：先锚点查询，再取全会话做 turn 切片。"""
    hits = load_events(agh_home, run_id=run_id, retries=retries, delay=delay)
    if not hits:
        raise EvidenceError(f"AGH 会话库中找不到 run_id={run_id} 的痕迹")
    session_keys = list(dict.fromkeys(h["session_key"] for h in hits))
    conn = sqlite3.connect(str(Path(agh_home) / "data" / "sessions.db"))
    conn.row_factory = sqlite3.Row
    try:
        candidates = []
        for session_key in session_keys:
            rows = conn.execute(
                "SELECT ts, type, session_key, data FROM events WHERE session_key=? ORDER BY rowid",
                (session_key,)).fetchall()
            try:
                candidates.append(build_index([dict(r) for r in rows], run_id))
            except EvidenceError:
                candidates.append({"run_id": run_id, "session_key": session_key, "turns": [],
                                   "model": [], "tool_calls": [], "checks": {},
                                   "window": {"last_ts": next(h["ts"] for h in reversed(hits) if h["session_key"] == session_key)}})
    finally:
        conn.close()
    if not candidates:
        raise EvidenceError(f"run_id={run_id} 没有可索引的工具执行")
    # Prefer the latest actual attempt; never silently attest an earlier failed session.
    index = max(candidates, key=lambda c: c["window"]["last_ts"])
    index["attempts"] = [{"session_key": c["session_key"], "turns": c["turns"],
                          "checks": c["checks"]} for c in candidates]
    return index


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
    for rows, endpoint in ((reports, WRITE_ENDPOINTS[0]), (tasks, WRITE_ENDPOINTS[1])):
        for row in rows:
            if not any(c.get("endpoint") == endpoint and c.get("is_error") is False
                       and (c.get("request_args") or {}).get("run_id") == index.get("run_id")
                       and (c.get("response") or {}).get("run_id") == index.get("run_id")
                       and (c.get("response") or {}).get("id") == row.get("id")
                       and row.get("run_id") == index.get("run_id")
                       for c in index.get("tool_calls", [])):
                errors.append(f"{endpoint.split('/')[-2]}#{row.get('id')} 无匹配成功响应（{endpoint}，id/run_id）")
    return errors

def validate_completion(index, reports, tasks, run_log=None):
    """Single completion contract for online settlement and evidence export."""
    from phmlite.autodiag import parse_evidence_steps
    from phmlite.diagnosis_guard import validate_diagnosis
    checks = index.get("checks") or {}
    problems, messages = [], []
    def require(ok, code, message):
        if not ok:
            problems.append(code)
            messages.append(message)
    require(len(reports) == 1, "report_count_invalid", "本次运行必须且只能有一份报告")
    require(len(tasks) == 1, "task_missing" if not tasks else "task_count_invalid", "完整闭环必须且只能有一份工单")
    problem = parse_evidence_steps(reports[0].get("evidence"))[1] if reports else "没有报告"
    require(problem is None, "evidence_invalid", f"证据结构不合规：{problem}")
    require(checks.get("tool_call_count", 0) >= 5, "tool_calls_insufficient", "完整诊断工具调用不足5次")
    require(bool(index.get("model")), "model_missing", "执行索引缺模型标识")
    require(checks.get("turn_completed") is True, "turn_incomplete", "执行turn没有成功结束")
    errors = validate_linkage(index, reports, tasks)
    require(not errors, "linkage_failed", "；".join(errors))
    needed = [WRITE_ENDPOINTS[0]] + ([WRITE_ENDPOINTS[1]] if tasks else [])
    writes_ok = all(checks.get("write_endpoints_ok", {}).get(e) is True for e in needed)
    require(writes_ok, "write_call_unverified", "业务写入缺匹配成功响应")
    diagnosis_problem = None
    if reports:
        rep = reports[0]
        checked_index = dict(index, http_audit=run_log) if run_log is not None else index
        diagnosis_problem = validate_diagnosis(rep.get("conclusion", ""), rep.get("diagnosis_status", "legacy"),
                                             rep.get("diagnosis_code"), checked_index)
        require(rep.get("diagnosis_status") == "confirmed", "diagnosis_limited", "保留性/未知报告不计完整诊断")
        require(diagnosis_problem is None, "diagnosis_unsupported", diagnosis_problem or "")
    if run_log is not None:
        require(all(any(r.get("tool") == e and r.get("run_id") == index.get("run_id")
                        and r.get("http_status") == 200 for r in run_log) for e in needed),
                "http_write_unverified", "缺少报告/工单HTTP200审计")
    limited_verified = bool(reports and reports[0].get("diagnosis_status") in ("provisional", "undetermined")
                            and not tasks and set(problems) == {"task_missing", "diagnosis_limited"})
    return {"ok": not problems, "limited_verified": limited_verified, "problems": problems, "messages": messages,
            "report": len(reports) == 1, "task_linked": len(tasks) == 1,
            "evidence_structure": problem is None, "evidence_problem": problem,
            "index_built": True, "tool_calls": checks.get("tool_call_count"),
            "tool_calls_ok": checks.get("tool_call_count", 0) >= 5,
            "write_calls_ok": writes_ok, "linkage_ok": not errors, "linkage_errors": errors,
            "closure_gap_only": problems == ["task_missing"] or limited_verified}
