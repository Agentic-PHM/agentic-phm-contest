"""P0 问题2 回归：run_id ↔ AGH 会话精确定位、执行索引、双向对账与证据包完整性。"""
import sqlite3
from pathlib import Path

import pytest

from phmlite.agh_evidence import (EvidenceError, build_index, session_window_events,
                                  validate_linkage)

SESSION = "agnes:local:local-dev:cli:workspace:6c5ae26b"


def _ev(ts, etype, data, session=SESSION):
    return {"ts": ts, "type": etype, "session_key": session, "data": json_dumps(data)}


def json_dumps(data):
    import json
    return json.dumps(data, ensure_ascii=False)


RUN_ID = "auto-1011-031333-29051e69"


def _synthetic_session(run_id=RUN_ID, with_tool_run_id=True):
    submit_cmd = f'curl.exe -s "http://127.0.0.1:8181/api/report/submit?run_id={run_id}&conclusion=x"'
    spectrum_cmd = 'curl.exe -s "http://127.0.0.1:8181/api/spectrum?point=DE&type=envelope"'
    return [
        _ev("2026-10-10T19:13:37Z", "user/message",
            {"content": [{"type": "text", "text": f"P-101 严重报警…run_id={run_id}…取证"}]}),
        _ev("2026-10-10T19:13:37Z", "turn/start", {"turn": 11}),
        _ev("2026-10-10T19:13:42Z", "tool/call",
            {"toolUseId": "t0", "name": "shell", "args": {"command": spectrum_cmd}, "ordinal": 0}),
        _ev("2026-10-10T19:13:44Z", "tool/result", {"toolUseId": "t0", "isError": False}),
        _ev("2026-10-10T19:13:45Z", "cost/ledger", {"model": "agnes-3.0-flash"}),
        _ev("2026-10-10T19:14:10Z", "tool/call",
            {"toolUseId": "t1", "name": "shell",
             "args": {"command": submit_cmd if with_tool_run_id else
                      'curl.exe -s "http://127.0.0.1:8181/api/report/submit?run_id=other&conclusion=x"'},
             "ordinal": 1}),
        _ev("2026-10-10T19:14:11Z", "tool/result", {"toolUseId": "t1", "isError": False}),
        _ev("2026-10-10T19:14:18Z", "turn/end", {"reason": "completed"}),
    ]


def test_build_index_locates_run_and_links_tools():
    idx = build_index(_synthetic_session(), RUN_ID)
    assert idx["session_key"] == SESSION and idx["turns"] == [11]
    assert idx["model"] == ["agnes-3.0-flash"]
    assert idx["checks"]["run_id_in_prompt"] and idx["checks"]["run_id_in_tool_args"]
    assert idx["checks"]["tool_call_count"] == 2
    assert [tc["tool_use_id"] for tc in idx["tool_calls"]] == ["t0", "t1"]
    assert idx["tool_calls"][1]["is_error"] is False


def test_build_index_raises_when_run_id_absent():
    with pytest.raises(EvidenceError):
        build_index(_synthetic_session(), "auto-9999-not-there")


def test_validate_linkage_detects_broken_chain():
    idx = build_index(_synthetic_session(with_tool_run_id=False), RUN_ID)
    # prompt 命中但工具参数没有 run_id → 业务↔执行断链必须被拦截
    errors = validate_linkage(idx, reports=[{"id": 1}], tasks=[{"id": 1}])
    assert any("工具参数" in e for e in errors)
    # 工单在会话中无 task/create 调用 → 拦截
    idx2 = build_index(_synthetic_session(), RUN_ID)
    errors2 = validate_linkage(idx2, reports=[{"id": 1}], tasks=[{"id": 1}])
    assert any("task/create" in e for e in errors2)
    # 干净会话 + 干净业务行 → 通过
    assert validate_linkage(idx2, reports=[], tasks=[]) == []


def _write_session_db(agh_home, events):
    """把合成事件写进临时 sessions.db（与 AGH 同构的表结构）。"""
    data_dir = Path(agh_home) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / "sessions.db")
    conn.execute("CREATE TABLE events (ts TEXT, type TEXT, session_key TEXT, data TEXT)")
    for ev in events:
        conn.execute("INSERT INTO events VALUES (?,?,?,?)",
                     (ev["ts"], ev["type"], ev["session_key"], ev["data"]))
    conn.commit()
    conn.close()


def test_exported_window_keeps_prompt_before_turn_start(tmp_path):
    """P0 回归：原始 Prompt（user/message）排在 turn/start 之前，导出必须带上它。

    导出侧曾自写一份 turn 切片、只保留 turn 内部事件，于是证据包里没有任何 user/message，
    而索引仍标 run_id_in_prompt=true——评委只能相信总结，无法独立核查"没见过真值"。
    """
    agh_home = tmp_path / "agh-home"
    _write_session_db(agh_home, _synthetic_session())
    idx = build_index(_synthetic_session(), RUN_ID)

    exported = session_window_events(agh_home, idx)
    types = [e["type"] for e in exported]

    assert idx["checks"]["run_id_in_prompt"] is True
    assert "user/message" in types, "证据包必须含原始 Prompt，否则索引断言不可独立核查"
    prompt = next(e for e in exported if e["type"] == "user/message")
    assert RUN_ID in prompt["data"]
    # 时序完整：Prompt 在 turn/start 之前，turn 边界事件都在窗口内
    assert types.index("user/message") < types.index("turn/start")
    assert "turn/end" in types


def test_exported_window_excludes_other_turns(tmp_path):
    """只导出命中的 turn 及其前导段，不把整个会话倒进证据包。"""
    unrelated = [
        _ev("2026-10-10T19:10:00Z", "user/message",
            {"content": [{"type": "text", "text": "无关会话"}]}),
        _ev("2026-10-10T19:10:00Z", "turn/start", {"turn": 10}),
        _ev("2026-10-10T19:10:05Z", "tool/call",
            {"toolUseId": "t9", "name": "shell", "args": {"command": "echo unrelated-turn"}}),
        _ev("2026-10-10T19:10:06Z", "turn/end", {"reason": "completed"}),
    ]
    events = unrelated + _synthetic_session()
    agh_home = tmp_path / "agh-home"
    _write_session_db(agh_home, events)
    idx = build_index(events, RUN_ID)
    assert idx["turns"] == [11]

    exported = session_window_events(agh_home, idx)
    blob = "\n".join(e["data"] or "" for e in exported)
    assert "echo unrelated-turn" not in blob, "未命中的 turn 不应进入证据包"
    assert "无关会话" not in blob
