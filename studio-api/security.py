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
  * Requests that never went through the front proxy at all. On a shared host
    (Open OnDemand) the proxy's cookie gate protects only the proxy port —
    uvicorn's own port is reachable by every other local user on the machine,
    and a loopback bind is not a boundary between users. See
    ``InternalSecretMiddleware``.
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

import hmac
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

    The order below is load-bearing.

    * **Allowlisted Origin** — allowed, and checked *first*. An operator naming
      an origin has made a decision that must beat every heuristic after it.
      Checking ``Sec-Fetch-Site`` ahead of this made the allowlist dead on
      arrival: a browser posting from an allowlisted origin necessarily labels
      the request ``cross-site`` or ``same-site``, which is exactly what the
      operator was overriding. The bug was invisible to tests because the ASGI
      test client sends no ``Sec-Fetch-Site`` at all.
    * ``Sec-Fetch-Site`` — ``cross-site`` and ``same-site`` are rejected. The
      browser has already classified the request, and it cannot be spoofed by
      the page making it.
    * **Origin absent** — allowed. A request with no Origin did not come from a
      browser acting on a page (curl, the test client, a local MCP client), and
      those cannot be driven cross-site by a victim's browser. Refusing them
      would break every non-browser caller to defend against an attacker who
      has no way to strip the header anyway.
    * **Origin present** — must be same-origin with the Host this request was
      addressed to. Browsers send Origin on every unsafe-method request,
      cross-site form submissions included, so this is the common case.
    """
    origin = headers.get("origin")

    # First, before any heuristic: an explicit operator allowlist.
    if origin and origin.rstrip("/") in allowed_origins():
        return True

    site = (headers.get("sec-fetch-site") or "").strip().lower()
    if site in ("cross-site", "same-site"):
        return False

    if not origin:
        return True

    origin_netloc = urlsplit(origin).netloc.rstrip("/")
    if not origin_netloc:
        # "null" (a sandboxed iframe, a file:// page) parses to no netloc. It is
        # never same-origin with anything, so it is never one of ours.
        return False

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


# --- Requests that bypassed the front proxy (#218) ----------------------------


def internal_secret() -> str | None:
    """The proxy-stamped header value this API requires when the gate is on.

    Generated by the entrypoint alongside ``STUDIO_AUTH_TOKEN`` and known only
    to the rendered proxy config. Read per request rather than captured at
    import, so tests see the current environment.
    """
    return os.environ.get("STUDIO_INTERNAL_SECRET") or None


class InternalSecretMiddleware:
    """Reject requests that did not come through this session's front proxy.

    The cookie gate lives in the proxy, which is enough when the container owns
    its network namespace. On a shared host it is not: uvicorn's port is
    reachable by any other local user on the machine, so the gate alone protects
    nothing. Binding loopback does not help either — loopback separates hosts,
    not users.

    So when the entrypoint turns the gate on it also mints
    ``STUDIO_INTERNAL_SECRET``, which appears in exactly two places: the
    rendered proxy config, which stamps it on every proxied request as
    ``X-Studio-Internal``, and this process's environment. A request without the
    right value did not come through the proxy.

    Raw ASGI on purpose. ``@app.middleware("http")`` never sees websocket
    scopes, and the terminal PTYs — an interactive shell as the session owner —
    are the first thing a local attacker would reach for. A websocket is refused
    by closing before accept.

    Compared with ``hmac.compare_digest``: the check is a secret comparison, and
    a plain ``==`` leaks its answer through timing.

    Unset secret — the K8s and plain-container path — is pure pass-through, so
    nothing changes for a deployment that does not opt in.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        secret = internal_secret()
        if secret is None:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"x-studio-internal", b"").decode("latin-1")
        if hmac.compare_digest(provided, secret):
            await self.app(scope, receive, send)
            return

        log.warning(
            "internal_secret_rejected",
            scope_type=scope["type"],
            path=scope.get("path"),
        )
        if scope["type"] == "http":
            await send({
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({
                "type": "http.response.body",
                "body": b'{"detail":"request did not come through the session '
                        b'proxy"}',
            })
        else:
            await send({"type": "websocket.close", "code": 4403})
