"""InternalSecretMiddleware — proxy-only access when the token gate is on.

The front proxy's cookie gate cannot protect uvicorn's own port: on a shared
native host any local user can hit it directly. When the launcher enables the
gate it exports STUDIO_INTERNAL_SECRET, which only the rendered proxy config
knows; every request without the proxy-stamped header must be rejected —
including websocket handshakes, which is exactly what a local attacker would
aim for (terminal PTYs).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from security import InternalSecretMiddleware


@pytest.fixture
def gated_app():
    app = FastAPI()
    app.add_middleware(InternalSecretMiddleware)

    @app.get("/api/ping")
    async def ping() -> dict:
        return {"ok": True}

    @app.websocket("/ws/echo")
    async def echo(ws: WebSocket) -> None:
        await ws.accept()
        await ws.send_text("hi")
        await ws.close()

    return app


def test_passthrough_when_unset(gated_app, monkeypatch):
    """K8s/container path: no secret, no behavior change."""
    monkeypatch.delenv("STUDIO_INTERNAL_SECRET", raising=False)
    client = TestClient(gated_app)
    assert client.get("/api/ping").status_code == 200
    with client.websocket_connect("/ws/echo") as ws:
        assert ws.receive_text() == "hi"


def test_http_requires_the_header(gated_app, monkeypatch):
    monkeypatch.setenv("STUDIO_INTERNAL_SECRET", "s3cret")
    client = TestClient(gated_app)
    assert client.get("/api/ping").status_code == 403
    assert (
        client.get("/api/ping", headers={"X-Studio-Internal": "wrong"}).status_code
        == 403
    )
    assert (
        client.get("/api/ping", headers={"X-Studio-Internal": "s3cret"}).status_code
        == 200
    )


def test_websocket_requires_the_header(gated_app, monkeypatch):
    """A plain http middleware never sees websocket scopes — this must not
    regress to one. Handshake without the header is rejected."""
    monkeypatch.setenv("STUDIO_INTERNAL_SECRET", "s3cret")
    client = TestClient(gated_app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/echo"):
            pass
    with client.websocket_connect(
        "/ws/echo", headers={"X-Studio-Internal": "s3cret"}
    ) as ws:
        assert ws.receive_text() == "hi"


def test_real_app_wires_the_middleware(client, monkeypatch):
    """The middleware must actually be installed on main.app, not just exist:
    with the secret set, even /api/health is proxy-only."""
    monkeypatch.setenv("STUDIO_INTERNAL_SECRET", "s3cret")
    assert client.get("/api/health").status_code == 403
    assert (
        client.get(
            "/api/health", headers={"X-Studio-Internal": "s3cret"}
        ).status_code
        == 200
    )
