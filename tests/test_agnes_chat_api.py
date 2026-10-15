"""Chat and action summaries must use AGH and must fail closed."""
import importlib
import json
import urllib.parse
import urllib.error
import urllib.request

import pytest

from phmlite.agh_runtime import AGHError


@pytest.fixture(autouse=True)
def prevent_direct_provider_network(monkeypatch):
    original = urllib.request.urlopen

    def blocked(*args, **kwargs):
        target = args[0].full_url if isinstance(args[0], urllib.request.Request) else str(args[0])
        parsed = urllib.parse.urlsplit(target)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("direct provider network call is forbidden in this test")
        return original(*args, **kwargs)

    monkeypatch.setattr("urllib.request.urlopen", blocked)


@pytest.fixture(autouse=True)
def mock_agh_daemon(monkeypatch):
    import phmlite.server as server

    monkeypatch.setattr(server, "agh_runtime_available", lambda: True)


def _chat_post(base_url, payload):
    request = urllib.request.Request(
        base_url + "/api/agent/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as response:
        return response.code, json.loads(response.read())


def test_ask_builds_chat_response_from_shared_agh_prompt(monkeypatch):
    from phmlite import agnes_chat

    calls = []

    def run_prompt(prompt, *, timeout, mode):
        calls.append((prompt, timeout, mode))
        return json.dumps({"reply": "我来查询通道。", "actions": [{"type": "query_channels"}]})

    monkeypatch.setattr(agnes_chat, "run_prompt", run_prompt, raising=False)

    result = agnes_chat.ask("查一下通道", [{"role": "assistant", "content": "可以。"}])

    assert result == {"reply": "我来查询通道。", "actions": [{"type": "query_channels"}]}
    assert len(calls) == 1 and calls[0][1:] == (agnes_chat.TIMEOUT, "text")
    assert "查一下通道" in calls[0][0] and "可以。" in calls[0][0]
    assert '"actions"' in calls[0][0] and "ACTIONS:[" not in calls[0][0]


def test_ask_rejects_invalid_json_instead_of_returning_a_friendly_success(monkeypatch):
    from phmlite import agnes_chat

    monkeypatch.setattr(agnes_chat, "run_prompt", lambda *args, **kwargs: "查好了！", raising=False)

    with pytest.raises(AGHError) as caught:
        agnes_chat.ask("查一下")

    assert caught.value.category == "invalid_response"


def test_ask_rejects_missing_reply_and_non_list_actions(monkeypatch):
    from phmlite import agnes_chat

    for response in ('{"actions": []}', '{"reply": "查好了", "actions": {}}'):
        monkeypatch.setattr(agnes_chat, "run_prompt", lambda *args, _response=response, **kwargs: _response,
                            raising=False)
        with pytest.raises(AGHError) as caught:
            agnes_chat.ask("查一下")
        assert caught.value.category == "invalid_response"


def test_ask_propagates_agh_failure_without_local_reply(monkeypatch):
    from phmlite import agnes_chat

    def offline(*args, **kwargs):
        raise AGHError("runtime_unavailable", "AGH daemon offline")

    monkeypatch.setattr(agnes_chat, "run_prompt", offline, raising=False)
    with pytest.raises(AGHError, match="AGH daemon offline"):
        agnes_chat.ask("查一下")


def test_summarize_uses_agh_and_propagates_failure(monkeypatch):
    from phmlite import agnes_chat

    calls = []
    monkeypatch.setattr(agnes_chat, "run_prompt", lambda prompt, **kwargs:
                        calls.append((prompt, kwargs)) or "16 路通道查询完成。", raising=False)
    summary = agnes_chat.summarize("查通道", "我来查。", [{"type": "query_channels", "ok": True, "total": 16}])
    assert summary == "16 路通道查询完成。"
    assert len(calls) == 1 and calls[0][1] == {"timeout": agnes_chat.TIMEOUT, "mode": "text"}

    monkeypatch.setattr(agnes_chat, "run_prompt", lambda *args, **kwargs:
                        (_ for _ in ()).throw(AGHError("timeout", "AGH 调用超时。")), raising=False)
    with pytest.raises(AGHError, match="AGH 调用超时"):
        agnes_chat.summarize("查通道", "我来查。", [])


def test_chat_endpoint_uses_agh_for_reply_and_action_summary(base_url, monkeypatch):
    from phmlite import agnes_chat

    prompts = []
    responses = iter([
        json.dumps({"reply": "我来查询通道。", "actions": [{"type": "query_channels"}]}),
        "16 路通道查询完成。",
    ])

    def run_prompt(prompt, **kwargs):
        prompts.append((prompt, kwargs))
        return next(responses)

    monkeypatch.setattr(agnes_chat, "run_prompt", run_prompt, raising=False)
    status, payload = _chat_post(base_url, {
        "run_id": "agh-chat-test", "message": "查一下通道", "history": [],
    })

    assert status == 200 and payload["reply"] == "16 路通道查询完成。"
    assert payload["actions"][0]["ok"] is True
    assert len(prompts) == 2


def test_chat_endpoint_reports_agh_failure_without_running_actions(base_url, monkeypatch):
    import phmlite.server as server
    from phmlite import agnes_chat

    actions = []
    monkeypatch.setattr(server, "_execute_agent_action", lambda action: actions.append(action))
    monkeypatch.setattr(agnes_chat, "run_prompt", lambda *args, **kwargs:
                        (_ for _ in ()).throw(AGHError("runtime_unavailable", "offline")), raising=False)

    status, payload = _chat_post(base_url, {"message": "注入故障", "history": []})

    assert status == 503 and actions == []
    assert "offline" not in json.dumps(payload)


def test_chat_endpoint_rejects_malformed_agh_response_without_running_actions(base_url, monkeypatch):
    import phmlite.server as server
    from phmlite import agnes_chat

    actions = []
    monkeypatch.setattr(server, "_execute_agent_action", lambda action: actions.append(action))
    monkeypatch.setattr(agnes_chat, "run_prompt", lambda *args, **kwargs: "ACTIONS:[坏 JSON", raising=False)

    status, payload = _chat_post(base_url, {"message": "注入故障", "history": []})

    assert status == 502 and actions == []
    assert "ACTIONS" not in json.dumps(payload)


def test_manual_mode_blocks_chat_before_calling_agh(base_url, monkeypatch, tmp_path):
    import phmlite.server as server
    from phmlite import agnes_chat

    try:
        set_mode = importlib.import_module("phmlite.operation_mode").set_mode
    except ImportError as exc:
        pytest.fail(f"persistent operation mode is missing: {exc}")

    monkeypatch.setattr(server.app.state, "data_dir", tmp_path)
    set_mode(tmp_path, "manual")
    calls = []
    monkeypatch.setattr(agnes_chat, "run_prompt", lambda *args, **kwargs:
                        calls.append(args) or json.dumps({"reply": "done", "actions": []}))

    status, payload = _chat_post(base_url, {"message": "检查一下", "history": []})

    assert status == 409 and calls == []
    assert "人工备用" in json.dumps(payload, ensure_ascii=False)
