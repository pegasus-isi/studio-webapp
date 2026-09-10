"""Origin/CORS helpers.

The studio frontend, REST API, and WebSocket endpoints are all served from a
single origin (one container port, behind the JupyterHub gateway). So normal
operation needs no cross-origin access at all — same-origin requests bypass CORS
entirely, and same-origin WebSocket handshakes match the Host header.

Three things still need protecting against *cross-site* attacks the gateway
cannot stop (its auth is cookie-based, which browsers attach automatically):

  * CORS: never reflect an arbitrary Origin while allowing credentials.
  * WebSocket: browsers don't apply the same-origin policy or CORS preflights to
    WebSockets, so an interactive-shell endpoint must validate the Origin header
    itself or it's open to Cross-Site WebSocket Hijacking.
  * Writes over plain HTTP: CORS decides whether a page may *read* a response,
    never whether the request runs. Anything needing no preflight executes
    cross-site with the gateway cookie attached — a path-only POST
    (``/api/tools/{id}/uninstall``), a POST whose body is entirely defaulted
    (workflow generate/build/plan/submit), a handler reading ``request.json()``
    without checking Content-Type (every mutating route in ``chat.py``). So
    unsafe methods get their own Origin check here (PAS-SEC-01).

Extra origins (e.g. a `next dev` server on :3000) can be allowed via the
STUDIO_ALLOWED_ORIGINS env var (comma-separated).
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import structlog
from fastapi import WebSocket
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

log = structlog.get_logger()


def allowed_origins() -> list[str]:
    """Explicit cross-origin allowlist from env (comma-separated). Empty by
    default — same-origin needs no entry here."""
    raw = os.environ.get("STUDIO_ALLOWED_ORIGINS", "")
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def _request_host(headers) -> str | None:
    """The host this request was addressed to, honoring a reverse proxy."""
    fwd = headers.get("x-forwarded-host")
    if fwd:
        # X-Forwarded-Host may be a comma-separated list; take the first.
        return fwd.split(",")[0].strip()
    return headers.get("host")


def is_ws_origin_allowed(websocket: WebSocket) -> bool:
    """True if a WebSocket handshake may proceed.

    Allowed when: no Origin header (non-browser client — curl, tests, native
    apps — which cannot be driven cross-site by a victim's browser); the Origin
    is same-origin with the request Host; or the Origin is in the explicit
    allowlist. Everything else is rejected as a potential CSWSH attempt.
    """
    origin = websocket.headers.get("origin")
    if not origin:
        return True

    origin_netloc = urlsplit(origin).netloc.rstrip("/")
    if not origin_netloc:
        return False

    if origin.rstrip("/") in allowed_origins():
        return True

    host = _request_host(websocket.headers)
    if host and origin_netloc.lower() == host.strip().rstrip("/").lower():
        return True

    log.warning("ws_origin_rejected", origin=origin, host=host)
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


# --- Cross-origin writes (PAS-SEC-01) ---------------------------------------

#: Methods that cannot change state, per RFC 9110. Everything else is a write.
#: OPTIONS is here so CORS preflight still reaches CORSMiddleware.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def is_write_origin_allowed(headers) -> bool:
    """True if a state-changing HTTP request may proceed.

    Mirrors the WebSocket rule, and rejects for the same reason: the gateway's
    session cookie rides along on any request a third-party page makes, so the
    only thing distinguishing "the user clicked something in Studio" from "the
    user visited a page that posted to Studio" is where the request came from.

    * **Origin present** — must be same-origin with the Host this request was
      addressed to, or explicitly allowlisted. Browsers send Origin on every
      unsafe-method request, cross-site form submissions included, so this is
      the case that matters.
    * **Origin absent** — allowed, unless ``Sec-Fetch-Site`` contradicts it. A
      request with no Origin did not come from a browser acting on a page
      (curl, the test client, a local MCP client), and those cannot be driven
      cross-site by a victim's browser. Refusing them would break every
      non-browser caller to defend against an attacker who has no way to strip
      the header anyway.
    * ``Sec-Fetch-Site`` is checked as a second signal where the browser sends
      it: ``cross-site`` and ``same-site`` are rejected even if Origin somehow
      looked acceptable.
    """
    site = (headers.get("sec-fetch-site") or "").strip().lower()
    if site in ("cross-site", "same-site"):
        return False

    origin = headers.get("origin")
    if not origin:
        return True

    origin_netloc = urlsplit(origin).netloc.rstrip("/")
    if not origin_netloc:
        # "null" (a sandboxed iframe, a file:// page) parses to no netloc. It is
        # never same-origin with anything, so it is never one of ours.
        return False

    if origin.rstrip("/") in allowed_origins():
        return True

    host = _request_host(headers)
    return bool(host and origin_netloc.lower() == host.strip().rstrip("/").lower())


class CrossOriginWriteMiddleware:
    """403 any state-changing request that a third-party page initiated.

    Raw ASGI rather than ``@app.middleware("http")`` so a websocket scope passes
    through untouched — those are checked at the handshake, where a close code
    can be sent, and wrapping them here would only add a second, weaker gate.

    ``STUDIO_ALLOWED_ORIGINS`` is the escape hatch for a legitimate cross-origin
    caller (a ``next dev`` server on :3000 proxies the browser's Origin through
    to the API, so local frontend development needs it listed).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if is_write_origin_allowed(headers):
            await self.app(scope, receive, send)
            return

        log.warning(
            "cross_origin_write_rejected",
            path=scope.get("path"),
            method=scope["method"],
            origin=headers.get("origin"),
            host=_request_host(headers),
            sec_fetch_site=headers.get("sec-fetch-site"),
        )
        response = JSONResponse(
            status_code=403,
            content={"detail": "Cross-origin request rejected"},
        )
        await response(scope, receive, send)
