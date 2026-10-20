"""routers/hub_auth: JupyterHub OAuth for the single-user server (#592)."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import pytest

from routers import hub_auth

PREFIX = "/user/alice/"


@pytest.fixture
def hub(monkeypatch):
    env = {
        "STUDIO_HUB_AUTH": "1",
        "JUPYTERHUB_USER": "alice",
        "JUPYTERHUB_CLIENT_ID": "jupyterhub-user-alice",
        "JUPYTERHUB_API_TOKEN": "hub-token-for-alice",
        "JUPYTERHUB_API_URL": "http://hub:8081/hub/api",
        "JUPYTERHUB_OAUTH_CALLBACK_URL": "/user/alice/oauth_callback",
        "JUPYTERHUB_SERVICE_PREFIX": PREFIX,
        "JUPYTERHUB_BASE_URL": "/",
        "JUPYTERHUB_PUBLIC_HUB_URL": "",
        "JUPYTERHUB_OAUTH_ACCESS_SCOPES": '["access:servers!server=alice/", "access:servers!user=alice"]',
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


def _session(user="alice", exp=None):
    return hub_auth._sign({"user": user, "sub": user, "exp": exp or time.time() + 60})


# ── /check ────────────────────────────────────────────────────────────────


def test_check_refuses_without_a_session(client, hub):
    assert client.get("/_hub_auth/check").status_code == 401


def test_check_accepts_a_valid_session(client, hub):
    client.cookies.set(hub_auth.SESSION_COOKIE, _session())
    assert client.get("/_hub_auth/check").status_code == 200


@pytest.mark.parametrize(
    "value",
    [
        "garbage",
        _session.__name__ + ".deadbeef",
    ],
)
def test_check_refuses_a_forged_cookie(client, hub, value):
    client.cookies.set(hub_auth.SESSION_COOKIE, value)
    assert client.get("/_hub_auth/check").status_code == 401


def test_check_refuses_a_tampered_or_expired_or_foreign_session(client, hub, monkeypatch):
    good = _session()
    body, mac = good.rsplit(".", 1)
    client.cookies.set(hub_auth.SESSION_COOKIE, body + "x." + mac)
    assert client.get("/_hub_auth/check").status_code == 401
    client.cookies.set(hub_auth.SESSION_COOKIE, _session(exp=time.time() - 1))
    assert client.get("/_hub_auth/check").status_code == 401
    # Signed for another server's user: not this one's.
    client.cookies.set(hub_auth.SESSION_COOKIE, _session(user="bob"))
    assert client.get("/_hub_auth/check").status_code == 401
    # Signed with another pod's key (another hub token).
    monkeypatch.setenv("JUPYTERHUB_API_TOKEN", "a-different-token")
    client.cookies.set(hub_auth.SESSION_COOKIE, good)
    assert client.get("/_hub_auth/check").status_code == 401


def test_everything_refuses_outside_hub_mode(client, hub, monkeypatch):
    client.cookies.set(hub_auth.SESSION_COOKIE, _session())
    monkeypatch.setenv("STUDIO_HUB_AUTH", "0")
    assert client.get("/_hub_auth/check").status_code == 401
    assert client.get("/_hub_auth/login").status_code == 401
    assert client.get("/_hub_auth/callback?code=x&state=y").status_code == 401


# ── /login ────────────────────────────────────────────────────────────────


def _login(client, uri, method="GET", upgrade=""):
    return client.get(
        "/_hub_auth/login",
        headers={"X-Original-URI": uri, "X-Original-Method": method, "X-Original-Upgrade": upgrade},
        follow_redirects=False,
    )


def test_a_page_load_goes_to_the_hubs_authorize_endpoint(client, hub):
    r = _login(client, "/user/alice/workflows?x=1")
    assert r.status_code == 302
    url = urlparse(r.headers["location"])
    assert url.path == "/hub/api/oauth2/authorize"
    q = parse_qs(url.query)
    assert q["client_id"] == ["jupyterhub-user-alice"]
    assert q["redirect_uri"] == ["/user/alice/oauth_callback"]
    assert q["response_type"] == ["code"] and len(q["state"][0]) >= 20
    cookie = r.headers["set-cookie"]
    assert hub_auth.STATE_COOKIE in cookie and "HttpOnly" in cookie and "Path=/user/alice/" in cookie


@pytest.mark.parametrize(
    "uri,method,upgrade",
    [
        ("/user/alice/api/health", "GET", ""),
        ("/user/alice/ws/terminal", "GET", "websocket"),
        ("/user/alice/api/files/write", "POST", ""),
        ("/user/alice/jupyter/api/terminals", "GET", ""),
    ],
)
def test_api_websocket_and_writes_get_401_not_a_redirect(client, hub, uri, method, upgrade):
    r = _login(client, uri, method, upgrade)
    assert r.status_code == 401 and "set-cookie" not in r.headers


def test_never_an_open_redirect(client, hub):
    for uri in ("//evil.example/x", "https://evil.example/", "/user/bob/"):
        r = _login(client, uri)
        state = client.cookies.get(hub_auth.STATE_COOKIE) or r.cookies.get(hub_auth.STATE_COOKIE)
        payload = hub_auth._verify(state)
        assert payload and payload["next"] == PREFIX, (uri, payload)


# ── /callback ─────────────────────────────────────────────────────────────


def _start(client):
    r = _login(client, "/user/alice/notebooks")
    # In production the hub calls back at /user/alice/oauth_callback, inside
    # the cookie's Path; the test client calls the route directly, so carry
    # the state cookie across the way the browser would.
    client.cookies.set(hub_auth.STATE_COOKIE, r.cookies[hub_auth.STATE_COOKIE])
    return parse_qs(urlparse(r.headers["location"]).query)["state"][0]


def _hub_says(monkeypatch, model):
    async def exchange(code):
        return "access-token" if code == "good-code" else None

    async def whoami(token):
        return model

    monkeypatch.setattr(hub_auth, "_exchange", exchange)
    monkeypatch.setattr(hub_auth, "_whoami", whoami)


def test_the_owner_gets_a_session_and_lands_where_they_were_going(client, hub, monkeypatch):
    _hub_says(monkeypatch, {"name": "alice", "scopes": []})
    state = _start(client)
    r = client.get(f"/_hub_auth/callback?code=good-code&state={state}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/user/alice/notebooks"
    cookie = r.headers["set-cookie"]
    assert "Path=/user/alice/" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie.replace("Lax", "lax")
    client.cookies.set(hub_auth.SESSION_COOKIE, r.cookies[hub_auth.SESSION_COOKIE])
    assert client.get("/_hub_auth/check").status_code == 200


def test_someone_else_is_refused(client, hub, monkeypatch):
    _hub_says(monkeypatch, {"name": "mallory", "scopes": ["read:users"]})
    state = _start(client)
    r = client.get(f"/_hub_auth/callback?code=good-code&state={state}", follow_redirects=False)
    assert r.status_code == 403
    assert client.get("/_hub_auth/check").status_code == 401


def test_an_admin_the_hub_granted_access_to_is_let_in(client, hub, monkeypatch):
    _hub_says(monkeypatch, {"name": "root", "scopes": ["access:servers!server=alice/"]})
    state = _start(client)
    r = client.get(f"/_hub_auth/callback?code=good-code&state={state}", follow_redirects=False)
    assert r.status_code == 302
    client.cookies.set(hub_auth.SESSION_COOKIE, r.cookies[hub_auth.SESSION_COOKIE])
    assert client.get("/_hub_auth/check").status_code == 200


def test_a_wrong_or_missing_state_starts_over_without_a_session(client, hub, monkeypatch):
    _hub_says(monkeypatch, {"name": "alice"})
    _start(client)
    r = client.get("/_hub_auth/callback?code=good-code&state=not-the-state", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == PREFIX
    assert client.get("/_hub_auth/check").status_code == 401


def test_a_code_the_hub_rejects_gives_no_session(client, hub, monkeypatch):
    _hub_says(monkeypatch, {"name": "alice"})
    state = _start(client)
    r = client.get(f"/_hub_auth/callback?code=bad-code&state={state}", follow_redirects=False)
    assert r.status_code == 403
    assert client.get("/_hub_auth/check").status_code == 401


def test_cookies_are_secure_behind_https_even_with_the_proxys_extra_hop(client, hub):
    r = client.get(
        "/_hub_auth/login",
        headers={"X-Original-URI": "/user/alice/", "X-Forwarded-Proto": "https,http"},
        follow_redirects=False,
    )
    assert "Secure" in r.headers["set-cookie"]
