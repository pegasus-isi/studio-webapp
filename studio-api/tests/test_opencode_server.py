"""Protect OpenCode server discovery, authentication, and failure handling.

The chat panel depends on these helpers staying deterministic when container
configuration is missing or malformed; regressions here could expose a bad
credential, probe the network unnecessarily, or make settings saves raise.
"""

from __future__ import annotations

import asyncio
import base64


def _run(coro):
    return asyncio.run(coro)


def test_port_honours_environment_and_falls_back_for_invalid_value(monkeypatch):
    from services import opencode_server

    monkeypatch.setenv("STUDIO_OPENCODE_PORT", "49152")
    assert opencode_server.port() == 49152

    monkeypatch.setenv("STUDIO_OPENCODE_PORT", "not-a-port")
    assert opencode_server.port() == 4096


def test_server_password_prefers_environment_then_file_then_none(
    monkeypatch, tmp_path
):
    from services import opencode_server

    monkeypatch.setenv("HOME", str(tmp_path))
    password_path = tmp_path / ".studio" / "opencode-password"
    password_path.parent.mkdir()
    password_path.write_text("file-secret\n")

    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "environment-secret")
    assert opencode_server.server_password() == "environment-secret"

    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD")
    assert opencode_server.server_password() == "file-secret"

    password_path.unlink()
    assert opencode_server.server_password() is None


def test_auth_header_is_empty_without_password_and_basic_with_password(monkeypatch):
    from services import opencode_server

    monkeypatch.setattr(opencode_server, "server_password", lambda: None)
    assert opencode_server.auth_header() == {}

    monkeypatch.setattr(opencode_server, "server_password", lambda: "secret")
    token = base64.b64encode(b"opencode:secret").decode()
    assert opencode_server.auth_header() == {
        "Authorization": f"Basic {token}"
    }


def test_health_distinguishes_no_password_from_not_listening(monkeypatch):
    from services import opencode_server

    monkeypatch.setattr(opencode_server, "server_password", lambda: None)
    no_password = _run(opencode_server.health())
    assert no_password["running"] is False
    assert no_password["reason"] == "no_password"

    monkeypatch.setattr(opencode_server, "server_password", lambda: "secret")
    monkeypatch.setattr(opencode_server, "listening", lambda: False)
    not_listening = _run(opencode_server.health())
    assert not_listening["running"] is False
    assert not_listening["reason"] == "not_listening"


def test_restart_returns_false_when_supervisorctl_is_missing(monkeypatch):
    from services import opencode_server

    calls = []

    async def missing_supervisorctl(*args, **kwargs):
        calls.append((args, kwargs))
        raise FileNotFoundError("supervisorctl")

    monkeypatch.setattr(
        opencode_server.asyncio,
        "create_subprocess_exec",
        missing_supervisorctl,
    )

    assert _run(opencode_server.restart()) is False
    assert len(calls) == 1
