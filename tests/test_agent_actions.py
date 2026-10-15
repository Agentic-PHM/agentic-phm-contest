"""任务③对话即操作：ACTIONS 协议解析 + 服务端白名单执行器（不触网、不调 LLM）。"""
import pytest

from phmlite.agnes_chat import _parse_actions
from phmlite.server import _execute_agent_action, app

# 执行器读写 app.state（db/scenario/channels），需先跑 conftest 的会话级服务器夹具
pytestmark = pytest.mark.usefixtures("base_url")


# ---- ACTIONS 协议解析 ----
def test_parse_actions_strips_line_and_parses():
    text = '好的，马上注入演示故障。\nACTIONS:[{"type":"inject_fault","fault":"bpfo","severity":0.8}]'
    reply, actions = _parse_actions(text)
    assert reply == "好的，马上注入演示故障。"
    assert actions == [{"type": "inject_fault", "fault": "bpfo", "severity": 0.8}]


def test_parse_actions_without_marker_or_broken_json():
    assert _parse_actions("今天轴承温度 45.1℃，一切正常。") == ("今天轴承温度 45.1℃，一切正常。", [])
    reply, actions = _parse_actions("好嘞\nACTIONS:[{坏掉的json")
    assert reply == "好嘞" and actions == []


# ---- 白名单执行器 ----
def test_query_channels_shape():
    r = _execute_agent_action({"type": "query_channels"})
    assert r["ok"] and r["total"] >= 16 and isinstance(r["abnormal"], list)


def test_inject_fault_whitelist_and_clamp():
    with pytest.raises(ValueError):
        _execute_agent_action({"type": "inject_fault", "fault": "rpfo"})  # 越权类型
    r = _execute_agent_action({"type": "inject_fault", "fault": "bpfi", "severity": 9})
    assert r["ok"] and r["severity"] == 1.0                     # 收敛到 0~1
    assert app.state.scenario.get()["fault"]["type"] == "bpfi"
    assert _execute_agent_action({"type": "recover_fault"})["ok"]
    assert app.state.scenario.get()["fault"]["type"] == "none"


def test_switch_view_and_latest_report():
    assert _execute_agent_action({"type": "switch_view", "view": "thermal"}) == \
        {"type": "switch_view", "ok": True, "view": "thermal"}
    with pytest.raises(ValueError):
        _execute_agent_action({"type": "switch_view", "view": "admin"})
    r = _execute_agent_action({"type": "latest_report"})
    assert r["ok"] and ("report" in r)


def test_unknown_action_rejected():
    with pytest.raises(ValueError):
        _execute_agent_action({"type": "delete_everything"})
