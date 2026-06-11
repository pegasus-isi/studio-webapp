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


def test_chat_stream_no_provider(client):
    """Streaming without LLM config should return an error SSE."""
    resp = client.post(
        "/api/chat/stream",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status_code == 200
    # Should be SSE with error
    text = resp.text
    assert "error" in text
    assert "LLM provider" in text or "configured" in text


def test_chat_stream_no_messages(client):
    resp = client.post("/api/chat/stream", json={"messages": []})
    assert resp.status_code == 200
    text = resp.text
    assert "No messages" in text
