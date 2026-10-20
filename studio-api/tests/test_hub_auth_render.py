"""The hub OAuth gate's nginx fragment, rendered by the real entrypoint (#592)."""

from __future__ import annotations

from tests.test_session_gate import _render

HUB = {
    "JUPYTERHUB_API_TOKEN": "t",
    "JUPYTERHUB_CLIENT_ID": "jupyterhub-user-alice",
    "JUPYTERHUB_OAUTH_CALLBACK_URL": "/user/alice/oauth_callback",
    "JUPYTERHUB_USER": "alice",
}


def _directives(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def test_off_without_a_hub(tmp_path):
    files = _render(tmp_path)
    assert _directives(files["hub-auth-server.conf"]) == []


def test_on_under_jupyterhub_and_gates_every_location(tmp_path):
    conf = _render(tmp_path, **HUB)["hub-auth-server.conf"]
    # Server-level, so every location inherits it, including ones added later.
    assert conf.splitlines()[0].startswith("#")
    assert "auth_request /_studio_hub_check;" in conf
    assert "error_page 401 = @studio_hub_login;" in conf
    # The gate's own locations opt out, and only they do.
    assert conf.count("auth_request off;") == 3
    assert "location ~ ^/user/[^/]+/oauth_callback$" in conf
    assert "internal;" in conf


def test_off_when_the_ood_token_gate_is_in_use(tmp_path):
    files = _render(tmp_path, STUDIO_AUTH_TOKEN="a" * 32, **HUB)
    assert _directives(files["hub-auth-server.conf"]) == []


def test_can_be_forced_off(tmp_path):
    files = _render(tmp_path, STUDIO_HUB_AUTH="0", **HUB)
    assert _directives(files["hub-auth-server.conf"]) == []


def test_included_before_the_user_locations(tmp_path):
    """Regex locations match in order: the oauth_callback location must come
    before the catch-all /user/ ones or the callback would be gated too."""
    proxy = _render(tmp_path, **HUB)["studio-proxy.conf"]
    inc = proxy.index("hub-auth-server.conf")
    assert inc < proxy.index("location ~ ^/user/[^/]+$")
    assert inc < proxy.index("(?<studio_api_path>")
