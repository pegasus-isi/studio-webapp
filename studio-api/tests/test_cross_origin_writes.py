"""A third-party page must not be able to drive a state-changing route.

PAS-SEC-01. Studio has no auth of its own: an upstream gateway authenticates the
browser with a session cookie, and the browser attaches that cookie to *any*
request a page makes to this origin. `CORSMiddleware` does not help — it decides
whether a page may read a response, never whether the request executes. So
anything that needs no preflight ran cross-site:

* a path-only POST — `POST /api/tools/{id}/uninstall`, which recursively
  deletes tool config under $HOME;
* a POST whose Pydantic body is entirely defaulted — workflow generate, build,
  plan, submit;
* a handler that reads `await request.json()` and so ignores Content-Type —
  every mutating route in `chat.py`, including one that starts an agent turn
  with attacker-supplied text.

The audit noted that no test asserted a cross-origin POST was rejected. These
do, on the real routes rather than a synthetic one, because the property worth
guarding is "the app refuses it", not "a helper function returns False".
"""

from __future__ import annotations

import pytest

# A page the user happened to be visiting.
EVIL = "https://evil.example.com"


def _post(client, path: str, origin: str | None = None, **headers):
    h = dict(headers)
    if origin is not None:
        h["Origin"] = origin
    return client.post(path, headers=h)


# --- The routes the audit actually named --------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/tools/claude-code/uninstall",
        "/api/tools/claude-code/start",
        "/api/chat/stop",
    ],
)
def test_cross_origin_write_is_rejected_on_real_routes(client, path):
    res = _post(client, path, origin=EVIL)
    assert res.status_code == 403, (path, res.status_code, res.text)
    assert res.json()["detail"] == "Cross-origin request rejected"


def test_uninstall_is_refused_before_it_can_delete_anything(client):
    """The worst of the chain: no body, so no preflight, and the handler
    recursively removes tool config under the user's real $HOME."""
    res = _post(client, "/api/tools/claude-code/uninstall", origin=EVIL)
    assert res.status_code == 403
    # 403 from the middleware, not a handler that ran and then failed.
    assert "detail" in res.json()


# --- What must keep working ---------------------------------------------------


def test_same_origin_write_passes(client):
    """The real app, hit the way the browser hits it.

    Asserting the handler's own answer, not merely "not 403": the middleware
    must be invisible here. 400 is the route rejecting an uninstalled tool,
    which it can only do once it has run.
    """
    res = _post(
        client, "/api/tools/claude-code/start", origin="http://testserver"
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "Tool not installed"


def test_no_origin_is_allowed(client):
    """curl, the test client, a local MCP client. None can be driven
    cross-site by a victim's browser, and refusing them would break every
    non-browser caller to stop an attacker who cannot set the header anyway."""
    res = _post(client, "/api/tools/claude-code/start")
    assert res.status_code == 400
    assert res.json()["detail"] == "Tool not installed"


def test_safe_methods_are_untouched(client):
    """GET changes nothing, and blocking cross-origin reads is CORS's job."""
    res = client.get("/api/health", headers={"Origin": EVIL})
    assert res.status_code == 200


def test_preflight_reaches_cors(client):
    """OPTIONS must pass through, or CORS can never answer a preflight."""
    res = client.options(
        "/api/tools/claude-code/start",
        headers={
            "Origin": EVIL,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert res.status_code != 403


def test_allowlisted_origin_is_accepted(client, monkeypatch):
    """`next dev` on :3000 proxies the browser's Origin through to the API, so
    local frontend development needs an explicit entry."""
    monkeypatch.setenv("STUDIO_ALLOWED_ORIGINS", "http://localhost:3000")
    res = _post(client, "/api/tools/claude-code/start", origin="http://localhost:3000")
    assert res.status_code == 400, res.text

    # And the allowlist is not a blanket opener: a different origin still 403s
    # while it is set.
    other = _post(client, "/api/tools/claude-code/start", origin=EVIL)
    assert other.status_code == 403


# --- The header logic itself --------------------------------------------------


def _headers(**kw):
    from starlette.datastructures import Headers

    return Headers({k.replace("_", "-"): v for k, v in kw.items()})


def test_sec_fetch_site_rejects_even_without_an_origin():
    """The second signal. A browser that sends Sec-Fetch-Site: cross-site has
    told us what this is regardless of what Origin says."""
    from security import is_write_origin_allowed

    assert not is_write_origin_allowed(_headers(sec_fetch_site="cross-site"))
    assert not is_write_origin_allowed(_headers(sec_fetch_site="same-site"))
    assert is_write_origin_allowed(_headers(sec_fetch_site="same-origin"))
    assert is_write_origin_allowed(_headers(sec_fetch_site="none"))


def test_null_origin_is_rejected():
    """A sandboxed iframe or a file:// page sends Origin: null. It parses to no
    netloc, and is never same-origin with anything."""
    from security import is_write_origin_allowed

    assert not is_write_origin_allowed(_headers(origin="null", host="studio.example"))


def test_forwarded_host_is_what_the_request_was_addressed_to():
    """Behind the gateway the Host header is the proxy's; X-Forwarded-Host is
    the origin the browser used, and comparing against the wrong one rejects
    every legitimate write in production."""
    from security import is_write_origin_allowed

    assert is_write_origin_allowed(
        _headers(
            origin="https://studio.pegasus-ai.org",
            host="127.0.0.1:8080",
            x_forwarded_host="studio.pegasus-ai.org",
        )
    )
    assert not is_write_origin_allowed(
        _headers(
            origin=EVIL,
            host="127.0.0.1:8080",
            x_forwarded_host="studio.pegasus-ai.org",
        )
    )


def test_forwarded_host_list_takes_the_first():
    from security import is_write_origin_allowed

    assert is_write_origin_allowed(
        _headers(
            origin="https://studio.pegasus-ai.org",
            x_forwarded_host="studio.pegasus-ai.org, inner.svc",
        )
    )


def test_origin_comparison_is_not_a_prefix_match():
    """`studio.pegasus-ai.org.evil.com` must not pass as the real host."""
    from security import is_write_origin_allowed

    assert not is_write_origin_allowed(
        _headers(
            origin="https://studio.pegasus-ai.org.evil.com",
            x_forwarded_host="studio.pegasus-ai.org",
        )
    )
