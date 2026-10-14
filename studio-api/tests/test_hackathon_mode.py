"""Hackathon kiosk mode (STUDIO_HACKATHON_MODE).

When set (1/true/yes), the deployment is a fixed demo surface: the chat
engine is locked to builtin, the tool marketplace is hidden and installs are
refused, and the Settings UI hides the corresponding controls. Default off:
unset means the full product, and every existing test asserts that shape.
"""

from __future__ import annotations


def _full_prefs(**overrides):
    base = {
        "simple_run": True,
        "advanced_open": False,
        "welcome_dismissed": True,
        "nav_advanced_open": False,
        "skip_welcome": False,
        "tour_on_login": False,
        "chat_engine": "builtin",
    }
    base.update(overrides)
    return base


def test_mode_endpoint_reports_flag_off_by_default(client):
    assert client.get("/api/settings/mode").json() == {"hackathon_mode": False}


def test_mode_endpoint_reports_flag_when_set(client, monkeypatch):
    monkeypatch.setenv("STUDIO_HACKATHON_MODE", "1")
    assert client.get("/api/settings/mode").json() == {"hackathon_mode": True}


def test_ui_get_forces_builtin_over_a_stored_opencode(client, monkeypatch):
    # A persistent home from before the event may store "opencode"; under the
    # flag the panel must still get builtin (store with the flag off so the
    # PUT itself goes through uncoerced).
    assert client.put("/api/settings/ui", json=_full_prefs(chat_engine="opencode")).status_code == 200
    assert client.get("/api/settings/ui").json()["chat_engine"] == "opencode"

    monkeypatch.setenv("STUDIO_HACKATHON_MODE", "1")
    assert client.get("/api/settings/ui").json()["chat_engine"] == "builtin"


def test_ui_put_coerces_engine_and_persists_builtin(client, monkeypatch):
    monkeypatch.setenv("STUDIO_HACKATHON_MODE", "1")
    saved = client.put("/api/settings/ui", json=_full_prefs(chat_engine="opencode"))
    assert saved.status_code == 200
    assert saved.json()["chat_engine"] == "builtin"

    # The stored row was coerced too, so lifting the flag later cannot
    # resurface a dead engine choice.
    monkeypatch.delenv("STUDIO_HACKATHON_MODE")
    assert client.get("/api/settings/ui").json()["chat_engine"] == "builtin"


def test_tools_list_empty_and_install_refused_under_flag(client, monkeypatch):
    monkeypatch.setenv("STUDIO_HACKATHON_MODE", "1")
    assert client.get("/api/tools").json() == {"tools": []}
    assert client.get("/api/tools/opencode").status_code == 404
    res = client.post("/api/tools/opencode/install")
    assert res.status_code == 403


def test_tools_untouched_without_flag(client):
    body = client.get("/api/tools").json()
    assert isinstance(body["tools"], list)
    # Unknown ids reach the installer (404), proving the request was not
    # short-circuited by any kiosk gate.
    assert client.post("/api/tools/no-such-tool/install").status_code == 404


def _seed_installed_opencode(tmp_path):
    """A pre-kiosk home: DB row says installed (binary state set per test)."""
    import asyncio

    from db import Database

    async def go():
        db = Database(str(tmp_path / "r.db"))
        await db.connect()
        await db.install_tool("opencode")
        return db

    return asyncio.run(go())


def test_reconcile_does_not_reinstall_under_flag(monkeypatch, tmp_path):
    """The live bug, pinned: DB says opencode/installed, binary absent (the
    entrypoint pruned it), kiosk on — no npm subprocess may spawn."""
    import asyncio
    import shutil

    import main

    db = _seed_installed_opencode(tmp_path)
    try:
        monkeypatch.setenv("STUDIO_HACKATHON_MODE", "1")
        monkeypatch.setattr(shutil, "which", lambda b: None)
        calls: list = []

        async def boom(*a, **k):
            calls.append(a)
            raise AssertionError("must not spawn npm under kiosk mode")

        monkeypatch.setattr(asyncio, "create_subprocess_shell", boom)
        monkeypatch.setattr(main, "db", db)
        asyncio.run(main._reconcile_installed_tools())
        assert calls == []
    finally:
        asyncio.run(db.close())


def test_reconcile_still_repairs_without_flag(monkeypatch, tmp_path):
    """Inverse: flag off keeps today's repair behavior (no vacuously-passing
    kiosk test — this proves the seam the flag guards)."""
    import asyncio
    import shutil

    import main

    db = _seed_installed_opencode(tmp_path)
    try:
        monkeypatch.delenv("STUDIO_HACKATHON_MODE", raising=False)
        monkeypatch.setattr(shutil, "which", lambda b: None)
        # Keep the PATH tweak in the sandbox: reconcile prepends the npm
        # bin dir on success, and that mutates the real test-process env.
        monkeypatch.setenv("NPM_CONFIG_PREFIX", str(tmp_path / "npfx"))
        calls: list = []

        class FakeProc:
            returncode = 0

        async def fake_shell(*a, **k):
            calls.append(a)
            return FakeProc()

        async def fake_comm(proc, timeout):
            return b"", b""

        monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
        monkeypatch.setattr(main, "communicate_or_kill", fake_comm)
        monkeypatch.setattr(main, "db", db)
        asyncio.run(main._reconcile_installed_tools())
        assert len(calls) == 1
        assert "opencode-ai" in calls[0][0]
    finally:
        asyncio.run(db.close())
