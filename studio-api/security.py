"""Same-origin helpers shared by the HTTP gate and the WebSocket guard.

The studio frontend, REST API, and WebSocket endpoints are all served from a
single origin (one container port, behind the JupyterHub gateway). So normal
operation needs no cross-origin access at all — same-origin requests bypass CORS
entirely, and same-origin WebSocket handshakes match the Host header.

Three things still need protecting against *cross-site* attacks the gateway
cannot stop (its auth is cookie-based, which browsers attach automatically):

  * CORS: never reflect an arbitrary Origin while allowing credentials.
  * HTTP mutations: CORS only governs whether a page may *read* a response. A
    "simple" request (form post, text/plain POST, a path-only or query-only
    POST) executes server-side with no preflight, so every non-safe method is
    gated on Origin / Sec-Fetch-Site by SameOriginMiddleware (PAS-SEC-01).
  * WebSocket: browsers don't apply the same-origin policy or CORS preflights to
    WebSockets, so an interactive-shell endpoint must validate the Origin header
    itself or it's open to Cross-Site WebSocket Hijacking.

Both gates make the same decision through is_origin_allowed(), so they can't
drift. Extra origins (e.g. a `next dev` server on :3000 talking to :8080
directly) can be allowed via the STUDIO_ALLOWED_ORIGINS env var
(comma-separated); it is also the escape hatch if a proxy chain presents a Host
the browser didn't send.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlsplit

import structlog
from fastapi import WebSocket
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = structlog.get_logger()

# Methods that must not change state (RFC 9110 §9.2.1). Everything else is
# gated. OPTIONS stays open so CORSMiddleware can answer preflights.
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

REJECT_DETAIL = "cross-origin request rejected"

_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}


def allowed_origins() -> list[str]:
    """Explicit cross-origin allowlist from env (comma-separated). Empty by
    default — same-origin needs no entry here."""
    raw = os.environ.get("STUDIO_ALLOWED_ORIGINS", "")
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def _request_host(headers: Mapping[str, str]) -> str | None:
    """The host this request was addressed to, honoring a reverse proxy.

    X-Forwarded-Host is consulted first: the `next dev` proxy rewrites Host to
    the API's own address and puts the browser-facing host here, so pinning to
    Host would 403 every mutation in `npm run dev`. A browser cannot forge it
    cross-site (forms can't set headers; a fetch() that sets it is no longer
    "simple", so the CORS preflight refuses it first).
    """
    fwd = headers.get("x-forwarded-host")
    if fwd:
        # X-Forwarded-Host may be a comma-separated list; take the first.
        return fwd.split(",")[0].strip()
    return headers.get("host")


def _host_port(netloc: str, scheme: str = "") -> tuple[str, int | None]:
    """Split 'host[:port]' → (lowercase hostname, port).

    An Origin without an explicit port gets its scheme default, so
    `https://studio.example.org` still matches a Host of
    `studio.example.org:443`. A malformed value (a port that isn't a number)
    yields a hostname nothing legitimate will match, rather than raising
    ValueError into the request path.
    """
    try:
        parts = urlsplit(f"//{netloc}")
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return netloc.lower(), None
    if port is None and scheme:
        port = _DEFAULT_PORTS.get(scheme.lower())
    return host, port


def is_origin_allowed(headers: Mapping[str, str]) -> bool:
    """The one same-origin decision, for HTTP and WebSocket alike.

    Allowed when: there is no Origin (non-browser client — curl, tests,
    in-container agents — which a victim's browser cannot drive cross-site);
    the Origin is in STUDIO_ALLOWED_ORIGINS; or the Origin's authority is the
    host this request was addressed to. `Origin: null` (sandboxed iframe,
    cross-origin-redirected POST) has no authority and is rejected.

    Port handling: the bundled nginx forwards `Host $host`, and `$host` never
    carries a port, so the API sees Host `localhost` for a browser sitting at
    http://localhost:8888 — which is exactly what ./local-test-instance.sh and
    any non-default-port deployment produce. An exact host:port compare would
    refuse every one of them. So: when the request Host has no explicit port,
    compare hostnames alone; when it has one, require an exact host:port match.
    Other ports of the public hostname are operator-controlled in every
    supported deployment, so this concedes nothing the gateway cookie doesn't
    already.
    """
    origin = (headers.get("origin") or "").strip()
    if not origin:
        return True

    parts = urlsplit(origin)
    origin_netloc = parts.netloc.rstrip("/")
    if not origin_netloc:
        return False  # "null" or malformed — no authority to compare

    if origin.rstrip("/") in allowed_origins():
        return True

    host = _request_host(headers)
    if not host:
        return False
    o_host, o_port = _host_port(origin_netloc, parts.scheme)
    h_host, h_port = _host_port(host.strip().rstrip("/"))
    if not o_host or o_host != h_host:
        return False
    return h_port is None or o_port == h_port


def http_cross_site_reason(method: str, headers: Mapping[str, str]) -> str | None:
    """Why a mutating HTTP request must be refused, or None to let it through.

    Two independent signals; either one rejects:

      * Origin present and not same-origin / allowlisted — the primary check.
        Browsers send Origin on every non-GET/HEAD request, same-origin ones
        included, and on HTML form posts.
      * Sec-Fetch-Site: cross-site — belt-and-braces for browsers that drop
        Origin on some form posts. Only that literal value rejects:
        `same-origin`, `same-site` (JupyterHub subdomain_host setups) and
        `none` (address-bar navigations) pass, and so does an absent header
        (older Safari/Firefox). An allowlisted Origin wins over this header so
        an operator who lists a genuinely cross-site dev origin isn't locked
        out by it.

    No Origin and no cross-site Sec-Fetch-Site means a non-browser client,
    which a victim's browser cannot drive — allowed.
    """
    if method.upper() in SAFE_METHODS:
        return None
    if not is_origin_allowed(headers):
        return "origin"
    origin = (headers.get("origin") or "").strip().rstrip("/")
    if origin and origin in allowed_origins():
        return None
    if (headers.get("sec-fetch-site") or "").strip().lower() == "cross-site":
        return "sec-fetch-site"
    return None


class SameOriginMiddleware:
    """Pure-ASGI gate: refuse cross-site mutating HTTP requests with 403.

    Deliberately not BaseHTTPMiddleware. That wrapper runs the downstream app
    in a separate task behind a memory stream and re-emits the body itself —
    exactly the machinery that has broken SSE across Starlette releases
    (buffering in old versions; today, client disconnect arrives as task-group
    cancellation rather than the GeneratorExit that routers/chat.py's event
    generators rely on to skip the trailing [DONE]). It also loses contextvars
    set downstream and adds a hop per streamed chunk. This class decides from
    scope + headers alone, before routing, then hands receive/send through
    untouched, so streaming, cancellation and request-body semantics are
    exactly what they were without it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # WebSocket handshakes are checked per-route by reject_ws_if_cross_origin
        # (they need a 4403 close frame, not a JSON 403); lifespan isn't a request.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        method = scope.get("method", "GET")
        reason = http_cross_site_reason(method, headers)
        if reason is None:
            await self.app(scope, receive, send)
            return

        # Warning, not info: the failure mode this gate can cause is "every
        # button in the UI 403s", and the only way to tell a real attack from a
        # proxy chain presenting a Host the browser didn't send is to have both
        # sides of the comparison in the pod log.
        log.warning(
            "http_cross_origin_rejected",
            method=method,
            path=scope.get("path"),
            origin=headers.get("origin"),
            host=_request_host(headers),
            sec_fetch_site=headers.get("sec-fetch-site"),
            reason=reason,
        )
        response = JSONResponse({"detail": REJECT_DETAIL}, status_code=403)
        await response(scope, receive, send)


def is_ws_origin_allowed(websocket: WebSocket) -> bool:
    """True if a WebSocket handshake may proceed (see is_origin_allowed)."""
    if is_origin_allowed(websocket.headers):
        return True
    log.warning(
        "ws_origin_rejected",
        origin=websocket.headers.get("origin"),
        host=_request_host(websocket.headers),
    )
    return False


async def reject_ws_if_cross_origin(websocket: WebSocket) -> bool:
    """Close the socket with 4403 if the Origin isn't allowed.

    Returns True if the handshake was rejected (caller should return), False if
    it's safe to proceed.
    """
    if is_ws_origin_allowed(websocket):
        return False
    await websocket.close(code=4403, reason="Origin not allowed")
    return True
