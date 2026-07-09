"""Tests for chat endpoints."""

from __future__ import annotations


def test_chat_history_empty(client):
    resp = client.get("/api/chat/history")
    assert resp.status_code == 200
    data = resp.json()
    assert "messages" in data
    assert isinstance(data["messages"], list)


def test_chat_agents(client):
    resp = client.get("/api/chat/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert "agents" in data


def test_chat_stop_unknown_is_noop(client):
    # An empty/unknown request_id must NOT cancel every active stream (that let
    # one tab kill another tab's turn). It's a no-op now.
    resp = client.post("/api/chat/stop", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "noop"


def test_chat_stop_all_explicit(client):
    # Stop-all is available only via the explicit flag.
    resp = client.post("/api/chat/stop", json={"all": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "stopped"
    assert data["cleared"] is True


def test_chat_stream_uses_baked_default(client):
    """With no saved LLM config, chat falls back to the baked-in gateway
    default rather than erroring that nothing is configured. (The gateway host
    isn't reachable from the test env, so the stream surfaces a connection
    error — but never the "not configured" message.)"""
    resp = client.post(
        "/api/chat/stream",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status_code == 200
    text = resp.text
    assert "data:" in text  # valid SSE stream
    assert "No LLM provider configured" not in text


def test_chat_stream_no_messages(client):
    resp = client.post("/api/chat/stream", json={"messages": []})
    assert resp.status_code == 200
    text = resp.text
    assert "No messages" in text


def test_history_scoped_by_conversation(client, monkeypatch):
    """Persisted messages are conversation-scoped and both roles are saved."""
    import routers.chat as chat

    async def fake_dispatch(*a, **k):
        yield {"type": "text", "text": "hi there"}
        yield {
            "type": "result",
            "result": {"content": "hi there", "tool_calls": [], "usage": {}},
        }

    monkeypatch.setattr(chat, "_openai_dispatch", fake_dispatch)

    client.post(
        "/api/chat/stream",
        json={
            "messages": [{"role": "user", "content": "hello A"}],
            "conversation_id": "conv-A",
        },
    )
    client.post(
        "/api/chat/stream",
        json={
            "messages": [{"role": "user", "content": "hello B"}],
            "conversation_id": "conv-B",
        },
    )

    a = client.get("/api/chat/history?conversation_id=conv-A").json()["messages"]
    roles = [m["role"] for m in a]
    contents = [m["content"] for m in a]
    # Both the user turn and the assistant reply are saved, scoped to conv-A.
    assert roles == ["user", "assistant"]
    assert "hello A" in contents
    assert "hello B" not in contents


def test_chat_stream_surfaces_truncation(client, monkeypatch):
    """A length-capped response is flagged to the user, not left mid-sentence."""
    import routers.chat as chat

    async def fake_dispatch(*a, **k):
        yield {"type": "text", "text": "partial"}
        yield {
            "type": "result",
            "result": {
                "content": "partial",
                "tool_calls": [],
                "usage": {},
                "stop_reason": "length",
            },
        }

    monkeypatch.setattr(chat, "_openai_dispatch", fake_dispatch)
    resp = client.post(
        "/api/chat/stream",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert "truncated at the output-token limit" in resp.text


def test_trim_history_keeps_recent_tail():
    import routers.chat as chat

    msgs = [{"role": "user", "content": str(i)} for i in range(100)]
    trimmed = chat._trim_history(msgs)
    assert len(trimmed) == chat._MAX_HISTORY_MESSAGES
    # The newest message (the current ask) is always retained.
    assert trimmed[-1]["content"] == "99"


def test_clip_tool_result_marks_truncation():
    import routers.chat as chat

    big = "x" * (chat._MAX_TOOL_RESULT_CHARS + 500)
    clipped = chat._clip_tool_result(big)
    assert len(clipped) < len(big)
    assert "truncated" in clipped
    assert chat._clip_tool_result("small") == "small"


def test_chat_stream_forwards_deltas(client, monkeypatch):
    """The endpoint forwards streamed text deltas (not one blocking chunk) and
    finishes with a usage/meta footer."""
    import routers.chat as chat

    async def fake_dispatch(*a, **k):
        yield {"type": "text", "text": "Hel"}
        yield {"type": "text", "text": "lo"}
        yield {
            "type": "result",
            "result": {
                "content": "Hello",
                "tool_calls": [],
                "usage": {"input": 1, "output": 2},
            },
        }

    monkeypatch.setattr(chat, "_openai_dispatch", fake_dispatch)
    resp = client.post(
        "/api/chat/stream",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    text = resp.text
    # Two separate delta events, not a single aggregated content event.
    assert '"content": "Hel"' in text
    assert '"content": "lo"' in text
    assert '"meta"' in text
    assert "[DONE]" in text


def test_chat_stream_retries_transient_drop(client, monkeypatch):
    """A transient transport drop *before any token* is retried, and the round
    then succeeds instead of surfacing an error."""
    import httpx

    import routers.chat as chat

    monkeypatch.setattr(chat, "_STREAM_RETRY_DELAY_S", 0)
    calls = {"n": 0}

    async def flaky_dispatch(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection reset")
        yield {"type": "text", "text": "ok"}
        yield {"type": "result", "result": {"content": "ok", "tool_calls": [], "usage": {}}}

    monkeypatch.setattr(chat, "_openai_dispatch", flaky_dispatch)
    resp = client.post(
        "/api/chat/stream",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert calls["n"] == 2  # one failure + one successful retry
    assert '"content": "ok"' in resp.text
    assert '"error"' not in resp.text


def test_chat_stream_gives_up_after_max_retries(client, monkeypatch):
    """Persistent transport failures surface a friendly, endpoint-named error
    after exhausting retries — not a bare "network error"."""
    import httpx

    import routers.chat as chat

    monkeypatch.setattr(chat, "_STREAM_RETRY_DELAY_S", 0)
    calls = {"n": 0}

    async def always_drops(*a, **k):
        calls["n"] += 1
        raise httpx.ConnectError("connection reset")
        yield  # pragma: no cover — make this an async generator

    monkeypatch.setattr(chat, "_openai_dispatch", always_drops)
    resp = client.post(
        "/api/chat/stream",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert calls["n"] == chat._MAX_STREAM_RETRIES + 1
    assert '"error"' in resp.text
    assert "transient" in resp.text.lower()


def test_chat_stream_no_retry_after_tokens(client, monkeypatch):
    """Once tokens have streamed, a mid-stream drop is NOT retried (that would
    duplicate content); it surfaces a mid-stream error instead."""
    import httpx

    import routers.chat as chat

    monkeypatch.setattr(chat, "_STREAM_RETRY_DELAY_S", 0)
    calls = {"n": 0}

    async def drops_midstream(*a, **k):
        calls["n"] += 1
        yield {"type": "text", "text": "partial"}
        raise httpx.ReadError("stream reset")

    monkeypatch.setattr(chat, "_openai_dispatch", drops_midstream)
    resp = client.post(
        "/api/chat/stream",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert calls["n"] == 1  # no retry after a token was emitted
    assert '"content": "partial"' in resp.text
    assert "partway through" in resp.text.lower()
