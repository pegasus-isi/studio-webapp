"""Tests for image-bundled default tool seeding (main._seed_default_tools)."""

from __future__ import annotations

import asyncio


def _run_seed(monkeypatch, tmp_path, *, which, default="opencode",
              preinstalled=False):
    """Run _seed_default_tools against a temp DB with a faked environment."""
    import main
    from db import Database

    calls: list[str] = []

    class FakeInstaller:
        async def install(self, tool_id, db):
            calls.append(tool_id)
            await db.install_tool(tool_id)
            return "installed"

    async def go():
        db = Database(str(tmp_path / "seed.db"))
        await db.connect()
        if preinstalled:
            await db.install_tool("opencode")

        monkeypatch.setenv("STUDIO_DEFAULT_TOOLS", default)
        monkeypatch.setattr(main, "db", db)
        import shutil as _shutil
        monkeypatch.setattr(_shutil, "which", lambda b: which.get(b))
        import routers.tools as tools_router
        monkeypatch.setattr(tools_router, "installer", FakeInstaller())

        await main._seed_default_tools()
        rows = {t["tool_id"] for t in await db.list_tools()}
        await db.close()
        return calls, rows

    return asyncio.run(go())


def test_seeds_bundled_tool(monkeypatch, tmp_path):
    calls, rows = _run_seed(
        monkeypatch, tmp_path, which={"opencode": "/opt/npm-global/bin/opencode"}
    )
    assert calls == ["opencode"]
    assert "opencode" in rows


def test_skips_when_binary_absent(monkeypatch, tmp_path):
    calls, rows = _run_seed(monkeypatch, tmp_path, which={})
    assert calls == []
    assert rows == set()


def test_skips_when_already_installed(monkeypatch, tmp_path):
    calls, _rows = _run_seed(
        monkeypatch, tmp_path,
        which={"opencode": "/opt/npm-global/bin/opencode"},
        preinstalled=True,
    )
    assert calls == []


def test_disabled_via_env(monkeypatch, tmp_path):
    calls, rows = _run_seed(
        monkeypatch, tmp_path,
        which={"opencode": "/opt/npm-global/bin/opencode"},
        default="",
    )
    assert calls == []
    assert rows == set()
