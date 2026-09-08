"""Protect the OpenCode proxy's transparent and isolated transport boundary.

These tests prevent regressions that could rewrite streamed agent responses,
drop request data, leak browser credentials to the local engine, or turn an
unavailable engine into an unhandled application error.
"""

from __future__ import annotations

import base64

import httpx

import routers.opencode as opencode_router


class _AsyncBytes(httpx.AsyncByteStream):
    """Present fixed bytes as an unread async response stream."""

    def __init__(self, content: bytes):
        self.content = content

    async def __aiter__(self):
        yield self.content


def _response(
    status_code: int,
    content: bytes,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers=headers,
        stream=_AsyncBytes(content),
    )


def _mock_upstream(monkeypatch, handler):
    """Patch the router's streaming client to use an in-memory transport."""
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr(opencode_router.httpx, "AsyncClient", factory)


def _set_password(monkeypatch, password: str = "proxy-secret") -> None:
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", password)
    monkeypatch.delenv("STUDIO_OPENCODE_PORT", raising=False)


def test_get_preserves_url_query_status_and_json_body(client, monkeypatch):
    _set_password(monkeypatch)
    seen = {}
    body = b'{"healthy":true,"version":"1.2.3"}'

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return _response(
            207,
            body,
            headers={"content-type": "application/json"},
        )

    _mock_upstream(monkeypatch, handler)

    res = client.get(
        "/api/opencode/global/health?filter=a%2Fb&raw=x+y&empty="
    )

    assert seen["url"] == (
        "http://127.0.0.1:4096/global/health?filter=a%2Fb&raw=x+y&empty="
    )
    assert res.status_code == 207
    assert res.content == body
    assert res.headers["content-type"] == "application/json"


def test_proxy_replaces_browser_credentials_with_server_basic_auth(
    client, monkeypatch
):
    password = "correct horse battery staple"
    _set_password(monkeypatch, password)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["cookie"] = request.headers.get("cookie")
        return _response(
            200,
            b'{"ok":true}',
            headers={"content-type": "application/json"},
        )

    _mock_upstream(monkeypatch, handler)

    res = client.get(
        "/api/opencode/session",
        headers={
            "Authorization": "Bearer browser-token",
            "Cookie": "studio-session=browser-cookie",
        },
    )

    token = base64.b64encode(f"opencode:{password}".encode()).decode()
    assert res.status_code == 200
    assert seen["authorization"] == f"Basic {token}"
    assert seen["cookie"] is None


def test_post_body_is_forwarded_byte_for_byte(client, monkeypatch):
    _set_password(monkeypatch)
    payload = b"\x00binary\xffpayload\r\n"
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return _response(200, b"accepted")

    _mock_upstream(monkeypatch, handler)

    res = client.post(
        "/api/opencode/session",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )

    assert res.status_code == 200
    assert seen["body"] == payload


def test_missing_password_returns_503_without_contacting_upstream(
    client, monkeypatch, tmp_path
):
    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    _mock_upstream(monkeypatch, handler)

    res = client.get("/api/opencode/session")

    assert res.status_code == 503
    assert calls == []


def test_upstream_connection_failure_returns_502(client, monkeypatch):
    _set_password(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _mock_upstream(monkeypatch, handler)

    res = client.get("/api/opencode/session")

    assert res.status_code == 502
    assert "unreachable" in res.json()["detail"].lower()


def test_event_stream_is_relayed_unbuffered_and_intact(client, monkeypatch):
    _set_password(monkeypatch)
    stream = b'data: {"type":"message","text":"hello"}\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(
            200,
            stream,
            headers={"content-type": "text/event-stream"},
        )

    _mock_upstream(monkeypatch, handler)

    res = client.get("/api/opencode/event")

    assert res.status_code == 200
    assert res.text == stream.decode()
    assert res.headers["x-accel-buffering"] == "no"


def test_unknown_studio_path_returns_404_without_forwarding(client, monkeypatch):
    _set_password(monkeypatch)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    _mock_upstream(monkeypatch, handler)

    res = client.get("/api/opencode/_studio/not-a-route")

    assert res.status_code == 404
    assert calls == []


def test_status_reports_no_password(client, monkeypatch, tmp_path):
    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    _mock_upstream(monkeypatch, handler)

    res = client.get("/api/opencode/_studio/status")

    assert res.status_code == 200
    assert res.json()["running"] is False
    assert res.json()["reason"] == "no_password"
    assert calls == []


def test_engine_specific_request_headers_are_forwarded(client, monkeypatch):
    """OpenCode's own headers must survive the hop.

    The SDK sends `x-opencode-directory` to scope a call to a project, so a
    proxy that forwarded only a header allowlist would silently answer every
    request from the server's default directory instead.
    """
    _set_password(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["directory"] = request.headers.get("x-opencode-directory")
        seen["accept"] = request.headers.get("accept")
        return _response(200, b"[]", headers={"content-type": "application/json"})

    _mock_upstream(monkeypatch, handler)

    res = client.get(
        "/api/opencode/session",
        headers={
            "x-opencode-directory": "%2Fhome%2Fu%2Fwork%2Fwf",
            "Accept": "text/event-stream",
        },
    )

    assert res.status_code == 200
    assert seen["directory"] == "%2Fhome%2Fu%2Fwork%2Fwf"
    assert seen["accept"] == "text/event-stream"


def test_accept_encoding_is_the_callers_choice_not_httpx_default():
    """A compressed event stream is a stalled event stream.

    httpx adds `gzip, deflate` to any request without an Accept-Encoding, so
    the proxy pins `identity` when the caller offered none — and forwards the
    caller's own value untouched when they did.
    """
    auth = {"Authorization": "Basic x"}

    silent = opencode_router._forward_headers({}, auth)
    assert silent["accept-encoding"] == "identity"

    asked = opencode_router._forward_headers({"accept-encoding": "gzip"}, auth)
    assert asked["accept-encoding"] == "gzip"


def test_forwarded_headers_drop_this_hops_own(monkeypatch):
    """Host, framing and the caller's credentials belong to studio's origin."""
    headers = opencode_router._forward_headers(
        {
            "host": "studio.pegasus-ai.org",
            "content-length": "12",
            "authorization": "Bearer browser-token",
            "cookie": "jupyterhub-session-id=abc",
            "connection": "keep-alive",
            "transfer-encoding": "chunked",
            "x-request-id": "keep-me",
        },
        {"Authorization": "Basic server-token"},
    )

    assert headers["Authorization"] == "Basic server-token"
    assert headers["x-request-id"] == "keep-me"
    for dropped in (
        "host",
        "content-length",
        "cookie",
        "connection",
        "transfer-encoding",
    ):
        assert dropped not in headers


def test_traversal_and_injection_cannot_leave_the_loopback_engine(
    client, monkeypatch
):
    """The proxy forwards a path; it must never forward somewhere else.

    A caller controls the whole path segment, so this pins the two things that
    would matter if it didn't hold: the request always goes to the loopback
    engine, and a path carrying a control character is refused rather than
    raising (httpx.InvalidURL is not an HTTPError, so it would otherwise be a
    500 with the upstream client never closed).
    """
    _set_password(monkeypatch)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return _response(200, b"{}", headers={"content-type": "application/json"})

    _mock_upstream(monkeypatch, handler)

    res = client.get("/api/opencode/..%2F..%2Fetc%2Fpasswd")
    assert res.status_code == 200
    assert seen[-1].host == "127.0.0.1"
    assert seen[-1].port == 4096

    res = client.get("/api/opencode/x%0d%0aX-Injected:%201")
    # Either the framework refuses the path or the proxy does, but it must not
    # be a 500 and must not reach the engine.
    assert res.status_code in (400, 404)
    assert all(url.host == "127.0.0.1" for url in seen)
