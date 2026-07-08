"""Origin/CORS helpers.

The studio frontend, REST API, and WebSocket endpoints are all served from a
single origin (one container port, behind the JupyterHub gateway). So normal
operation needs no cross-origin access at all — same-origin requests bypass CORS
entirely, and same-origin WebSocket handshakes match the Host header.

Two things still need protecting against *cross-site* attacks the gateway cannot
stop (its auth is cookie-based, which browsers attach automatically):

  * CORS: never reflect an arbitrary Origin while allowing credentials.
  * WebSocket: browsers don't apply the same-origin policy or CORS preflights to
    WebSockets, so an interactive-shell endpoint must validate the Origin header
    itself or it's open to Cross-Site WebSocket Hijacking.

Extra origins (e.g. a `next dev` server on :3000) can be allowed via the
STUDIO_ALLOWED_ORIGINS env var (comma-separated).
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import structlog
from fastapi import WebSocket

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
