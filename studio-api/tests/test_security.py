"""Tests for WebSocket Origin validation (CSWSH protection)."""

from __future__ import annotations

import security


class _FakeWS:
    def __init__(self, headers: dict[str, str]):
        # Header lookups in the helper are lower-case; normalize here.
        self.headers = {k.lower(): v for k, v in headers.items()}


def test_no_origin_allowed():
    # Non-browser clients (curl, tests, native apps) send no Origin and can't
    # be driven cross-site by a victim's browser.
    assert security.is_ws_origin_allowed(_FakeWS({"host": "studio.example.org"}))


def test_same_origin_allowed():
    ws = _FakeWS(
        {"host": "studio.example.org", "origin": "https://studio.example.org"}
    )
    assert security.is_ws_origin_allowed(ws)


def test_cross_origin_rejected():
    ws = _FakeWS(
        {"host": "studio.example.org", "origin": "https://evil.example.com"}
    )
    assert not security.is_ws_origin_allowed(ws)


def test_forwarded_host_honored():
    ws = _FakeWS(
        {
            "host": "internal:8888",
            "x-forwarded-host": "studio.example.org",
            "origin": "https://studio.example.org",
        }
    )
    assert security.is_ws_origin_allowed(ws)


def test_allowlist_env(monkeypatch):
    monkeypatch.setenv("STUDIO_ALLOWED_ORIGINS", "http://localhost:3000")
    ws = _FakeWS({"host": "localhost:8000", "origin": "http://localhost:3000"})
    assert security.is_ws_origin_allowed(ws)


def test_allowlist_env_does_not_admit_others(monkeypatch):
    monkeypatch.setenv("STUDIO_ALLOWED_ORIGINS", "http://localhost:3000")
    ws = _FakeWS({"host": "localhost:8000", "origin": "http://localhost:9999"})
    assert not security.is_ws_origin_allowed(ws)


# --- SEC-2: tool web UIs must bind loopback only ---


def test_web_tool_commands_bind_loopback_only():
    """nginx (8888) is the only exposed port; a web tool on 0.0.0.0 is
    reachable cross-pod on a flat network with no NetworkPolicy."""
    from services.installer import _INLINE_REGISTRY

    for tool in _INLINE_REGISTRY:
        cmd = tool.get("web_command", "")
        assert "0.0.0.0" not in cmd, f"{tool['id']} binds a non-loopback host"
        if cmd:
            assert "127.0.0.1" in cmd


def test_find_free_port_probes_loopback():
    from services.process_mgr import find_free_port

    assert isinstance(find_free_port(), int)


# --- SEC-5: generated Codex config.toml must be injection-proof ---


def test_codex_config_toml_survives_hostile_values():
    """Quotes and (crucially) newlines in provider fields must not inject
    TOML keys — parse the result and check values round-trip."""
    import tomllib

    from knowledge.codex_cli import _build_config_toml

    hostile_key = 'k"ey\nexperimental_use_shell = true'
    toml_text = _build_config_toml(
        active_provider="custom x!",
        active_model='mo"del\n[injected]',
        providers=[{
            "provider_id": "custom x!",
            "name": 'na"me\ninjected = 1',
            "api_key": hostile_key,
            "base_url": "https://x/v1",
        }],
    )
    parsed = tomllib.loads(toml_text)  # must be valid TOML — no key injection
    prov = parsed["model_providers"]["custom-x-"]
    assert prov["experimental_bearer_token"] == hostile_key
    assert "injected" not in prov
    assert "injected" not in parsed
    # model_provider must still point at the (sanitized) provider table.
    assert parsed["model_provider"] == "custom-x-"


# --- SEC-6: whoami header trust is opt-in ---


def test_whoami_ignores_auth_header_by_default(client, monkeypatch):
    monkeypatch.delenv("JUPYTERHUB_USER", raising=False)
    monkeypatch.delenv("STUDIO_TRUST_AUTH_HEADER", raising=False)
    r = client.get("/api/whoami", headers={"X-Auth-User": "mallory"})
    assert r.status_code == 200
    assert r.json()["user"] is None


def test_whoami_trusts_header_when_opted_in(client, monkeypatch):
    monkeypatch.delenv("JUPYTERHUB_USER", raising=False)
    monkeypatch.setenv("STUDIO_TRUST_AUTH_HEADER", "1")
    r = client.get("/api/whoami", headers={"X-Auth-User": "alice"})
    assert r.json()["user"] == "alice"


def test_whoami_prefers_server_injected_user(client, monkeypatch):
    monkeypatch.setenv("JUPYTERHUB_USER", "real-user")
    monkeypatch.setenv("STUDIO_TRUST_AUTH_HEADER", "1")
    r = client.get("/api/whoami", headers={"X-Auth-User": "mallory"})
    assert r.json()["user"] == "real-user"
