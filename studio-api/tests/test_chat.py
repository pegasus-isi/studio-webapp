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


def test_chat_stop(client):
    resp = client.post("/api/chat/stop", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "stopped"


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
