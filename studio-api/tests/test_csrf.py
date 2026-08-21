"""Same-origin gate for mutating HTTP requests (PAS-SEC-01 / NEW-05).

TestClient sends Host: testserver and no Origin, so the whole existing suite
exercises the "non-browser client" branch. These tests add the browser cases.

Pass-through assertions are `!= 403` on purpose: what the handler does after the
gate (200/400/404/415/422) belongs to the router tests, not here. Bodies are
sent as JSON so a handler's own Content-Type / required-body checks are never
what is being measured.
"""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import WebSocketDisconnect

import security
from security import REJECT_DETAIL, SameOriginMiddleware, http_cross_site_reason

EVIL = "https://evil.example"
SAME = "http://testserver"  # TestClient base_url → Host: testserver

# One representative of every preflight-free mutating shape the audit listed
# under NEW-05: path-only POST, query-only POST, default-body POST, raw
# request.json() POST, DELETE, PUT.
MUTATIONS = [
    ("POST", "/api/tools/claude-code/uninstall", None),
    ("POST", "/api/knowledge/marketplace/update", {}),
    ("POST", "/api/workflows/projects/nope/build", {"push": False}),
    ("POST", "/api/workflows/projects/nope/plan", {}),
    ("POST", "/api/workflows/nope/cancel", None),
    ("POST", "/api/chat/stream", {"messages": []}),
    (
        "POST",
        "/api/chat/permission",
        {"request_id": "r", "call_id": "c", "decision": "approve"},
    ),
    ("POST", "/api/chat/stop", {"all": True}),
    ("POST", "/api/notifications", {"title": "x"}),
    ("DELETE", "/api/notifications", None),
    ("PUT", "/api/llm/config", {"provider": "gateway"}),
    ("PUT", "/api/chat/command-approval", {"require_approval": True}),
]
_IDS = [f"{m} {p}" for m, p, _ in MUTATIONS]


@pytest.fixture
def client(client, monkeypatch, tmp_path):
    # marketplace/update must not look for a real checkout; a non-git dir makes
    # it answer {"updated": false, "reason": "not a git checkout"} without
    # touching the network.
    plain = tmp_path / "mkt"
    (plain / ".claude-plugin").mkdir(parents=True)
    monkeypatch.setenv("KNOWLEDGE_MARKETPLACE", str(plain))
    return client


def _send(client, method, path, body, headers):
    kwargs: dict = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return client.request(method, path, **kwargs)


@pytest.mark.parametrize("method,path,body", MUTATIONS, ids=_IDS)
def test_cross_origin_mutation_rejected(client, method, path, body):
    r = _send(client, method, path, body, {"Origin": EVIL})
    assert r.status_code == 403
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": REJECT_DETAIL}


@pytest.mark.parametrize("method,path,body", MUTATIONS, ids=_IDS)
def test_same_origin_mutation_passes(client, method, path, body):
    assert _send(client, method, path, body, {"Origin": SAME}).status_code != 403


@pytest.mark.parametrize("method,path,body", MUTATIONS, ids=_IDS)
def test_no_origin_mutation_passes(client, method, path, body):
    assert _send(client, method, path, body, {}).status_code != 403


def test_simple_request_shape_is_rejected_before_any_handler(client):
    """A text/plain POST needs no preflight, so CORS never sees it — this is
    the exact shape a third-party page uses, and the gate is what stops it."""
    r = client.post(
        "/api/tools/claude-code/uninstall",
        content="",
        headers={"Origin": EVIL, "Content-Type": "text/plain"},
    )
    assert r.status_code == 403
    r = client.post(
        "/api/chat/stop",
        content='{"all": true}',
        headers={"Origin": EVIL, "Content-Type": "text/plain"},
    )
    assert r.status_code == 403


def test_null_origin_rejected(client):
    r = client.post("/api/chat/stop", json={}, headers={"Origin": "null"})
    assert r.status_code == 403


@pytest.mark.parametrize(
    "method,path",
    [("GET", "/api/health"), ("GET", "/api/tools"), ("HEAD", "/api/health")],
)
def test_safe_methods_ignore_origin(client, method, path):
    assert client.request(method, path, headers={"Origin": EVIL}).status_code != 403


def test_allowlisted_origin_admitted(client, monkeypatch):
    monkeypatch.setenv("STUDIO_ALLOWED_ORIGINS", "https://dev.example:3000")
    r = client.post(
        "/api/chat/stop", json={}, headers={"Origin": "https://dev.example:3000"}
    )
    assert r.status_code != 403
    assert (
        client.post("/api/chat/stop", json={}, headers={"Origin": EVIL}).status_code
        == 403
    )


def test_next_dev_proxy_forwarded_host_admitted(client):
    """`npm run dev` proxies /api to the backend with changeOrigin, so Host is
    the API's own address and the browser-facing host arrives in X-Forwarded-
    Host. Pinning to Host would 403 every mutation in dev."""
    r = client.post(
        "/api/chat/stop",
        json={},
        headers={
            "Origin": "http://localhost:3000",
            "X-Forwarded-Host": "localhost:3000",
        },
    )
    assert r.status_code != 403


def test_forwarded_host_does_not_admit_a_foreign_origin(client):
    r = client.post(
        "/api/chat/stop",
        json={},
        headers={"Origin": EVIL, "X-Forwarded-Host": "studio.example.org"},
    )
    assert r.status_code == 403


def test_port_tolerated_when_proxy_drops_it(client):
    """nginx forwards `Host $host`, which never carries a port: a browser at
    http://host:8888 reaches the API as Host `host`. Hostname must still match."""
    assert (
        client.post(
            "/api/chat/stop", json={}, headers={"Origin": "http://testserver:8888"}
        ).status_code
        != 403
    )
    assert (
        client.post(
            "/api/chat/stop", json={}, headers={"Origin": "http://other:8888"}
        ).status_code
        == 403
    )


def test_sec_fetch_site_cross_site_rejected_without_origin(client):
    r = client.post("/api/chat/stop", json={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert r.json() == {"detail": REJECT_DETAIL}


@pytest.mark.parametrize("value", ["same-origin", "same-site", "none"])
def test_sec_fetch_site_benign_values_pass(client, value):
    r = client.post(
        "/api/chat/stop", json={}, headers={"Origin": SAME, "Sec-Fetch-Site": value}
    )
    assert r.status_code != 403


def test_sec_fetch_site_ignored_on_safe_methods(client):
    r = client.get("/api/health", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200


def test_allowlist_wins_over_sec_fetch_site(client, monkeypatch):
    monkeypatch.setenv("STUDIO_ALLOWED_ORIGINS", "https://dev.example:3000")
    r = client.post(
        "/api/chat/stop",
        json={},
        headers={"Origin": "https://dev.example:3000", "Sec-Fetch-Site": "cross-site"},
    )
    assert r.status_code != 403


def test_sse_route_still_streams_through_the_gate(client):
    """The reason the gate is pure ASGI rather than BaseHTTPMiddleware: a
    StreamingResponse must reach the client unchanged."""
    r = client.post("/api/chat/stream", json={"messages": []}, headers={"Origin": SAME})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "No messages" in r.text


def test_websocket_route_keeps_its_own_4403(client):
    """WS handshakes bypass the gate (non-http scope) and are still refused by
    the per-route guard with a close frame, not a JSON 403."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/terminal", headers={"Origin": EVIL}):
            pass
    assert exc.value.code == 4403


def test_middleware_passes_non_http_scopes_through():
    seen: list[str] = []

    async def inner(scope, receive, send) -> None:
        seen.append(scope["type"])

    async def run() -> None:
        mw = SameOriginMiddleware(inner)
        for kind in ("websocket", "lifespan"):
            await mw(
                {"type": kind, "headers": [(b"origin", EVIL.encode())]}, None, None
            )

    asyncio.run(run())
    assert seen == ["websocket", "lifespan"]


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "TRACE", "get"])
def test_reason_none_for_safe_methods(method):
    assert http_cross_site_reason(method, {"origin": EVIL, "host": "x"}) is None


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"host": "studio.example.org"}, None),
        ({"host": "studio.example.org", "origin": "https://studio.example.org"}, None),
        (
            {"host": "studio.example.org", "origin": "https://evil.example.com"},
            "origin",
        ),
        ({"host": "studio.example.org", "origin": "null"}, "origin"),
        (
            {"host": "studio.example.org", "sec-fetch-site": "cross-site"},
            "sec-fetch-site",
        ),
        ({"host": "studio.example.org", "sec-fetch-site": "same-site"}, None),
        ({"host": "localhost", "origin": "http://localhost:8888"}, None),
        ({"host": "localhost:8080", "origin": "http://localhost:3000"}, "origin"),
        (
            {"host": "studio.example.org:443", "origin": "https://studio.example.org"},
            None,
        ),
    ],
)
def test_reason_matrix(headers, expected):
    assert http_cross_site_reason("POST", headers) == expected


def test_ws_and_http_share_one_decision():
    """One helper decides both, so the two gates cannot drift apart."""

    class _WS:
        headers = {"host": "studio.example.org", "origin": "https://evil.example.com"}

    assert not security.is_ws_origin_allowed(_WS())
    assert http_cross_site_reason("POST", _WS.headers) == "origin"
