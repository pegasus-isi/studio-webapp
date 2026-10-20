"""JupyterHub OAuth for the single-user server Studio runs as (#592).

Under JupyterHub the hub's proxy routes /user/<name>/* to the user's pod and
authenticates nothing itself; the single-user server is meant to check every
request against the hub, which `jupyterhub-singleuser` does with HubOAuth.
Studio's entrypoint runs supervisord instead, so nothing checked, and any
request that reached the proxy reached Studio: terminal, Jupyter, the API.

This is the same flow HubOAuth runs, done with plain HTTP because the image
does not ship the `jupyterhub` package. KubeSpawner already gives the pod
everything it needs: JUPYTERHUB_CLIENT_ID, JUPYTERHUB_API_TOKEN (the client
secret), JUPYTERHUB_API_URL, JUPYTERHUB_OAUTH_CALLBACK_URL, JUPYTERHUB_USER
and JUPYTERHUB_OAUTH_ACCESS_SCOPES.

nginx (docker/nginx/hub-auth-server.conf.tmpl) asks `/check` about every
request. On 401 it hands the request to `/login`, which sends a page load to
the hub's authorize endpoint and answers anything else with 401. The hub
redirects back to `/callback`, which exchanges the code, asks the hub who
the user is, and sets a signed session cookie for this server's prefix.

These routes are mounted outside /api and are reached only through nginx's
internal locations and the oauth_callback location. When the pod is not in
hub mode (STUDIO_HUB_AUTH != "1": Open OnDemand, local dev) they refuse.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

log = structlog.get_logger()

router = APIRouter(prefix="/_hub_auth", include_in_schema=False)

SESSION_COOKIE = "studio-hub-auth"
STATE_COOKIE = "studio-hub-oauth-state"
# Short on purpose: renewing is one invisible redirect while the user's hub
# session lasts, and a shorter session bounds what a stolen cookie is worth.
SESSION_SECONDS = 8 * 3600
STATE_SECONDS = 600
_HUB_TIMEOUT = 15


# ── configuration ──────────────────────────────────────────────────────────


def enabled() -> bool:
    return os.environ.get("STUDIO_HUB_AUTH") == "1"


def _env(name: str) -> str:
    return os.environ.get(name, "")


def _prefix() -> str:
    """This server's URL prefix, e.g. /user/alice/ (always ends in /)."""
    p = _env("JUPYTERHUB_SERVICE_PREFIX") or f"/user/{_env('JUPYTERHUB_USER')}/"
    return p if p.endswith("/") else p + "/"


def _key() -> bytes:
    """Cookie signing key, derived from this server's hub token: stable for
    the pod's life, unknown to anything but the pod and the hub, and rotated
    whenever the server restarts (which logs everyone out, by design)."""
    return hmac.new(_env("JUPYTERHUB_API_TOKEN").encode(), b"studio-hub-auth-v1", hashlib.sha256).digest()


def _access_scopes() -> set[str]:
    raw = _env("JUPYTERHUB_OAUTH_ACCESS_SCOPES")
    try:
        value = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        value = []
    return {str(s) for s in value} if isinstance(value, list) else set()


# ── signed values ──────────────────────────────────────────────────────────


def _sign(payload: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def _verify(value: str | None) -> dict[str, Any] | None:
    if not value or "." not in value:
        return None
    body, mac = value.rsplit(".", 1)
    good = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, good):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or float(payload.get("exp", 0)) < time.time():
        return None
    return payload


def _secure(request: Request) -> bool:
    """Whether the browser reached the hub over HTTPS. Envoy sets
    X-Forwarded-Proto: https, and JupyterHub's proxy may append its own hop
    ("https,http"), so any https in the chain counts."""
    hops = request.headers.get("x-forwarded-proto", "").lower().replace(" ", "").split(",")
    return "https" in hops


def _set_cookie(resp: Response, request: Request, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(
        name, value, max_age=max_age, path=_prefix(), httponly=True,
        secure=_secure(request), samesite="lax",
    )


def session_user(request: Request) -> str | None:
    """The hub user this request's session cookie was issued to, if valid."""
    payload = _verify(request.cookies.get(SESSION_COOKIE))
    if not payload or payload.get("user") != _env("JUPYTERHUB_USER"):
        return None
    return str(payload.get("sub") or payload["user"])


# ── hub calls (patched in tests) ───────────────────────────────────────────


async def _exchange(code: str) -> str | None:
    """Authorization code -> access token, at the hub's token endpoint."""
    async with httpx.AsyncClient(timeout=_HUB_TIMEOUT) as client:
        r = await client.post(
            f"{_env('JUPYTERHUB_API_URL').rstrip('/')}/oauth2/token",
            data={
                "client_id": _env("JUPYTERHUB_CLIENT_ID"),
                "client_secret": _env("JUPYTERHUB_API_TOKEN"),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _env("JUPYTERHUB_OAUTH_CALLBACK_URL"),
            },
        )
    if r.status_code != 200:
        log.warning("hub_oauth_token_failed", status=r.status_code)
        return None
    return r.json().get("access_token")


async def _whoami(token: str) -> dict[str, Any] | None:
    """The hub's user model for an access token (name, scopes)."""
    async with httpx.AsyncClient(timeout=_HUB_TIMEOUT) as client:
        r = await client.get(
            f"{_env('JUPYTERHUB_API_URL').rstrip('/')}/user",
            headers={"Authorization": f"token {token}"},
        )
    if r.status_code != 200:
        log.warning("hub_oauth_whoami_failed", status=r.status_code)
        return None
    model = r.json()
    return model if isinstance(model, dict) else None


def _allowed(model: dict[str, Any]) -> bool:
    """The server's owner, or someone the hub granted access to this server
    (an admin with access:servers), exactly as HubOAuth decides."""
    if model.get("name") == _env("JUPYTERHUB_USER"):
        return True
    scopes = {str(s) for s in model.get("scopes") or []}
    return bool(scopes & _access_scopes())


# ── routes ─────────────────────────────────────────────────────────────────


@router.get("/check")
async def check(request: Request) -> Response:
    """nginx auth_request target: 200 with a valid session, else 401."""
    if not enabled():
        return Response(status_code=401)
    return Response(status_code=200 if session_user(request) else 401)


def _authorize_url(state: str) -> str:
    hub = (_env("JUPYTERHUB_PUBLIC_HUB_URL").rstrip("/") or "") + (_env("JUPYTERHUB_BASE_URL") or "/")
    hub = hub if hub.endswith("/") else hub + "/"
    query = urlencode({
        "client_id": _env("JUPYTERHUB_CLIENT_ID"),
        "redirect_uri": _env("JUPYTERHUB_OAUTH_CALLBACK_URL"),
        "response_type": "code",
        "state": state,
    })
    return f"{hub}hub/api/oauth2/authorize?{query}"


def _safe_next(uri: str) -> str:
    """Only paths under this server's prefix: never an open redirect."""
    prefix = _prefix()
    if uri.startswith(prefix) and not uri.startswith("//"):
        return uri
    return prefix


@router.get("/login")
async def login(request: Request) -> Response:
    """Where nginx sends a request without a valid session.

    A page load goes to the hub (one invisible hop while the hub session
    lasts). An API call, a WebSocket upgrade or a non-GET gets 401: a
    redirect to an HTML login is useless to fetch() and to a socket.
    """
    if not enabled():
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    uri = request.headers.get("x-original-uri", "")
    method = request.headers.get("x-original-method", "GET").upper()
    upgrade = request.headers.get("x-original-upgrade", "")
    rel = uri[len(_prefix()):] if uri.startswith(_prefix()) else uri.lstrip("/")
    if method != "GET" or upgrade or rel.startswith(("api/", "ws/", "jupyter/api/")):
        return JSONResponse(
            {"detail": "Not authenticated. Reload the page to sign in through the hub."},
            status_code=401,
        )
    state = secrets.token_urlsafe(24)
    resp = RedirectResponse(_authorize_url(state), status_code=302)
    _set_cookie(
        resp, request, STATE_COOKIE,
        _sign({"state": state, "next": _safe_next(uri), "exp": time.time() + STATE_SECONDS}),
        STATE_SECONDS,
    )
    return resp


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "") -> Response:
    """The hub's redirect after authorize: verify, exchange, set the session."""
    if not enabled():
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    saved = _verify(request.cookies.get(STATE_COOKIE))
    if not saved or not state or not hmac.compare_digest(str(saved.get("state", "")), state) or not code:
        # A missing or stale state is most often a second tab or an old link:
        # start again rather than show an error page.
        log.info("hub_oauth_bad_state")
        return RedirectResponse(_prefix(), status_code=302)
    token = await _exchange(code)
    model = await _whoami(token) if token else None
    if not model or not _allowed(model):
        log.warning("hub_oauth_denied", user=(model or {}).get("name"))
        return JSONResponse({"detail": "This Studio belongs to someone else."}, status_code=403)
    resp = RedirectResponse(_safe_next(str(saved.get("next") or "")), status_code=302)
    _set_cookie(
        resp, request, SESSION_COOKIE,
        _sign({"user": _env("JUPYTERHUB_USER"), "sub": model.get("name"), "exp": time.time() + SESSION_SECONDS}),
        SESSION_SECONDS,
    )
    resp.delete_cookie(STATE_COOKIE, path=_prefix())
    log.info("hub_oauth_session", user=model.get("name"))
    return resp
