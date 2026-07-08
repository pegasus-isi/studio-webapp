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
