"""Verbatim reverse proxy to the OpenCode agent server (#367).

The chat panel is a client of `opencode serve`, and this is the only way it
reaches it. Two things follow from that, and both are the point:

**Nothing here parses OpenCode's payloads.** Requests and responses pass
through as bytes, including the event stream. A new OpenCode feature — a new
message part type, a new endpoint — reaches the browser with no change on the
Python side. The engine this replaces did the opposite: it translated
OpenCode's events into Studio's own chat vocabulary, and needed five fix
commits for stale session maps, keepalives, model naming and error framing. The
test of this file is that bumping the server's version requires no edit to it.

**The browser never talks to the agent server directly.** That is what makes
the URL-prefix problem disappear (under KubeSpawner the app lives at
`/user/<name>/…`, and OpenCode's own client drops path prefixes), and it is
also the security boundary: the agent server binds loopback, holds the user's
full file and shell authority, and is reachable only through the same
authenticated origin as the rest of studio-api.

No path allowlist, unlike routers/pegasus_bot.py. That proxy fronts a shared
cluster service; this one fronts a per-user process running as the very user
whose browser is calling, on an origin an upstream ingress has already
authenticated — the same authority studio-api's own files and terminal
endpoints already hand out.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx
import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from services import opencode_server

log = structlog.get_logger()

router = APIRouter(prefix="/api/opencode", tags=["opencode"])

# Studio's own routes live under this prefix so they can never collide with a
# path OpenCode adds later. Everything else is forwarded untouched.
_STUDIO_PREFIX = "_studio/"

# Per-connection headers, meaningless to the upstream request/response and
# actively harmful when copied (a forwarded Transfer-Encoding on a body httpx
# has already de-chunked, say).
_HOP_BY_HOP = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
})
# Dropped on the way in as well: Host belongs to studio's origin, Content-Length
# is recomputed from the body we forward, and the credentials are ours to set —
# a browser Authorization header or the hub session cookie has no business
# reaching a local agent process.
_DROP_REQUEST = _HOP_BY_HOP | {"host", "content-length", "authorization", "cookie"}

# No read timeout: the event stream is open for the length of a session, and a
# turn can think for minutes before its first byte. The connect timeout is what
# turns "engine is down" into a fast 502 instead of a hung panel.
_TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0)


@router.get("/_studio/status")
async def status() -> dict[str, object]:
    """Whether the agent engine is usable, and why not when it isn't.

    Studio's own endpoint, not a translation of anything OpenCode returns: the
    panel has to be able to say "the engine isn't running" rather than showing
    a failed fetch.
    """
    health = await opencode_server.health()
    return {**health, "port": opencode_server.port()}


@router.post("/_studio/restart")
async def restart() -> dict[str, object]:
    """Restart the agent engine.

    Exposed because a config change only takes effect on restart (see
    docs/OPENCODE-CHAT-SPIKE.md), and because it is the fix for a password
    mismatch — both states the status endpoint reports and a user would
    otherwise have no way to clear.
    """
    ok = await opencode_server.restart()
    return {"restarted": ok}


def _forward_headers(incoming: Mapping[str, str], auth: dict[str, str]) -> dict[str, str]:
    """The headers to send upstream: the caller's, minus ours to decide.

    Everything the caller sent is forwarded — `x-opencode-directory` scopes a
    call to a project, so an allowlist here would quietly answer from the
    server's default directory instead — except the ones that belong to this
    hop rather than to the request.

    `Accept-Encoding` is set to `identity` only when the caller offered none.
    httpx would otherwise add `gzip, deflate` on the caller's behalf, which is
    not what a verbatim proxy does: the upstream then compresses a response
    nobody asked to have compressed, and a compressed event stream is one of
    the ways SSE stalls behind a proxy, because a compressor holds bytes until
    it flushes.
    """
    headers = dict(auth)
    for name, value in incoming.items():
        if name.lower() not in _DROP_REQUEST:
            headers[name] = value
    headers.setdefault("accept-encoding", "identity")
    return headers


def _upstream_url(path: str, request: Request) -> str:
    """The agent server URL for this request, query string preserved exactly.

    The host is always the loopback engine: `path` is only ever appended, so a
    caller cannot steer the request elsewhere — `//evil.com/x` becomes
    `http://127.0.0.1:4096/evil.com/x` and `@evil.com` stays in the path. The
    query string is passed through raw rather than re-encoded, because
    re-encoding is a change in meaning for a proxy that promises not to make
    any.
    """
    url = f"{opencode_server.base_url()}/{path.lstrip('/')}"
    query = request.url.query
    return f"{url}?{query}" if query else url


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy(path: str, request: Request) -> Response:
    """Forward one request to the agent server and stream the reply back."""
    if path.startswith(_STUDIO_PREFIX):
        # A studio-namespaced path that matched no route above. Answering 404
        # here rather than forwarding keeps the namespace meaningful.
        return JSONResponse({"detail": f"Unknown endpoint: {path}"}, status_code=404)

    headers = opencode_server.auth_header()
    if not headers:
        return JSONResponse(
            {
                "detail": (
                    "The chat engine has no server password yet. Restart the "
                    "container, or check ~/.studio/opencode-password."
                )
            },
            status_code=503,
        )
    headers = _forward_headers(request.headers, headers)

    body = await request.body()
    client = httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        # Inside the try with the send: building the request can fail on its
        # own — httpx rejects a path carrying a control character, and
        # InvalidURL is not an HTTPError — and an exception escaping here would
        # be a 500 with this client never closed.
        upstream_request = client.build_request(
            request.method,
            _upstream_url(path, request),
            headers=headers,
            content=body or None,
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.InvalidURL as e:
        await client.aclose()
        log.warning("opencode_proxy_bad_path", path=path, error=str(e))
        return JSONResponse({"detail": f"Invalid path: {e}"}, status_code=400)
    except httpx.HTTPError as e:
        await client.aclose()
        log.warning("opencode_proxy_failed", path=path, error=str(e))
        return JSONResponse(
            {"detail": f"Chat engine unreachable: {e}"}, status_code=502
        )

    async def relay():
        # aiter_raw, not aiter_bytes: the response headers are forwarded
        # verbatim, so the bytes have to stay in whatever encoding they claim.
        # Closing both the response and the client here is what keeps a
        # navigation away from an open event stream from leaking a connection.
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        # Content-Length would describe the pre-relay body; Starlette sets the
        # framing for a streamed response itself.
        if name.lower() not in _HOP_BY_HOP and name.lower() != "content-length"
    }
    # SSE only survives nginx unbuffered. The agent server already sets this;
    # asserting it here means a future upstream that forgets does not silently
    # turn streaming into a stall behind the proxy.
    if upstream.headers.get("content-type", "").startswith("text/event-stream"):
        response_headers["X-Accel-Buffering"] = "no"
        response_headers["Cache-Control"] = "no-cache"

    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        headers=response_headers,
    )
