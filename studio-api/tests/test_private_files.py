"""Files holding credentials are private to the user (#600).

Homes are created 0755 and the umask is 022, so on the hosted service every
default-mode file in a home is readable by the other accounts that mount it,
other users' local-universe jobs on the access point included. The saved LLM
keys live in three tool configs and the settings DB.
"""

from __future__ import annotations

import asyncio
import os
import re
import stat
import subprocess

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ENTRYPOINT = os.path.join(_REPO, "docker", "entrypoint.sh")

_KEYED = {
    "provider": "custom-x",
    "model": "m",
    "api_key": "sk-secret",
    "base_url": "https://llm.example.edu/v1",
    "providers": [{
        "id": "custom-x", "type": "custom", "name": "X", "api_key": "sk-secret",
        "base_url": "https://llm.example.edu/v1", "default_model": "m",
    }],
}


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    os.chmod(h, 0o755)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("STUDIO_WORKSPACE_DIR", raising=False)
    old = os.umask(0o022)
    yield h
    os.umask(old)


def test_write_private_is_0600_in_a_0700_dir_even_over_a_0644_file(tmp_path):
    from knowledge.common import write_private

    d = tmp_path / "cfg"
    d.mkdir(mode=0o755)
    f = d / "c.json"
    f.write_text("old")
    os.chmod(f, 0o644)
    # A stale temp file from a killed writer must not keep its mode either.
    stale = d / f"c.json.{os.getpid()}.tmp"
    stale.write_text("")
    os.chmod(stale, 0o644)
    write_private(str(f), "new")
    assert f.read_text() == "new"
    assert _mode(f) == 0o600
    assert _mode(d) == 0o700


@pytest.mark.parametrize("adapter,rel", [
    ("knowledge.opencode:OpenCodeAdapter", ".config/opencode/opencode.json"),
    ("knowledge.codex_cli:CodexCLIAdapter", ".codex/config.toml"),
    ("knowledge.crush:CrushAdapter", ".config/crush/crush.json"),
])
def test_tool_configs_with_keys_are_private(home, adapter, rel):
    import importlib

    mod, cls = adapter.split(":")
    ws = home / "work"
    ws.mkdir()
    getattr(importlib.import_module(mod), cls)().update_llm_config(str(ws), _KEYED)
    path = home / rel
    assert "sk-secret" in path.read_text()
    assert _mode(path) == 0o600, oct(_mode(path))
    assert _mode(path.parent) == 0o700


def test_settings_db_is_private(home):
    from db import Database

    path = home / ".studio" / "studio.db"

    async def go():
        db = Database(db_path=str(path))
        await db.connect()
        await db.close()

    asyncio.run(go())
    assert _mode(path.parent) == 0o700
    assert _mode(path) == 0o600


def _private_state_block() -> str:
    with open(_ENTRYPOINT, encoding="utf-8") as fh:
        script = fh.read()
    m = re.search(r"^# ---- Private state ----\n(.*?)^unset _priv\n", script, re.M | re.S)
    assert m, "private-state block not found in docker/entrypoint.sh"
    return m.group(0)


def test_entrypoint_locks_down_existing_homes(home, tmp_path):
    studio = home / ".studio"
    oc = home / ".config" / "opencode"
    for d in (studio, oc):
        d.mkdir(parents=True, mode=0o755)
        os.chmod(d, 0o755)
    db = studio / "studio.db"
    db.write_text("")
    os.chmod(db, 0o644)
    # A symlinked entry is left alone: chmod follows links, and this one
    # points outside the home.
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    os.symlink(outside, home / ".codex")

    proc = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + _private_state_block()],
        env={**os.environ, "HOME": str(home), "STUDIO_DIR": str(studio)},
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert _mode(studio) == 0o700
    assert _mode(oc) == 0o700
    assert _mode(db) == 0o600
    assert _mode(outside) == 0o755
