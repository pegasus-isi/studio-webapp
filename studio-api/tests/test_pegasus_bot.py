"""Tests for the PegasusAI Bot same-origin proxy."""

from __future__ import annotations

import httpx

import routers.pegasus_bot as pb


def _mock_upstream(monkeypatch, handler):
    """Patch the router's httpx client to route to a MockTransport."""
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr(pb.httpx, "AsyncClient", factory)


def test_proxy_forwards_search(client, monkeypatch):
    monkeypatch.setenv("PEGASUS_BOT_SERVER_URL", "http://server.ai-bot:8000")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"status": "ok", "response": "hi"})

    _mock_upstream(monkeypatch, handler)

    res = client.post(
        "/api/pegasus-bot/search", json={"request_id": "r1", "source": "x"}
    )
    assert res.status_code == 200
    assert res.json()["response"] == "hi"
    assert seen["url"] == "http://server.ai-bot:8000/search"
    assert "r1" in seen["body"]


def test_proxy_forwards_feedback(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    _mock_upstream(monkeypatch, handler)
    res = client.post("/api/pegasus-bot/feedback", json={"request_id": "r1"})
    assert res.status_code == 200


def test_proxy_rejects_unknown_path(client):
    # Only the extension's known endpoints are forwarded — not an open proxy.
    res = client.post("/api/pegasus-bot/admin", json={})
    assert res.status_code == 404


def test_proxy_502_when_upstream_unreachable(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    _mock_upstream(monkeypatch, handler)
    res = client.post("/api/pegasus-bot/search", json={})
    assert res.status_code == 502
    assert "unreachable" in res.json()["detail"].lower()
