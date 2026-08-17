"""Tests for global (HOME-scoped) tool-config propagation.

Studio's PVC mounts at ``~``; a terminal may start anywhere, so per-tool config
must live in the tool's global/user location (not only in ``~/work``). These
tests cover the claude_code + antigravity adapters mirroring config to HOME and
doing so non-destructively (never clobbering the user's own global files).
"""

from __future__ import annotations

import json
import os


def _seed_knowledge(root):
    """Create a minimal KNOWLEDGE_ROOT with the files the adapters copy."""
    os.makedirs(os.path.join(root, "references"), exist_ok=True)
    os.makedirs(os.path.join(root, "mcp"), exist_ok=True)
    with open(os.path.join(root, "references", "PEGASUS_AI.md"), "w") as f:
        f.write("# Pegasus AI\nKnowledge body.\n")
    with open(os.path.join(root, "mcp", "servers.json"), "w") as f:
        json.dump(
            {"mcpServers": {"pegasus-docs": {"type": "http", "url": "u1"}}}, f
        )


def _read(path):
    with open(path) as f:
        return f.read()


# --- common helpers ---------------------------------------------------------


def test_managed_block_preserves_user_content(tmp_path):
    from knowledge import common

    p = tmp_path / "CLAUDE.md"
    p.write_text("# My own notes\nkeep me\n")

    common.upsert_managed_block(str(p), "studio v1")
    text = _read(str(p))
    assert "# My own notes" in text
    assert "keep me" in text
    assert "studio v1" in text

    # Idempotent: re-running replaces the block, doesn't stack it.
    common.upsert_managed_block(str(p), "studio v2")
    text = _read(str(p))
    assert text.count(common._BLOCK_BEGIN) == 1
    assert "studio v2" in text
    assert "studio v1" not in text

    # Removal restores the user's content and drops the block.
    common.remove_managed_block(str(p))
    text = _read(str(p))
    assert "keep me" in text
    assert common._BLOCK_BEGIN not in text


def test_managed_block_removes_empty_file(tmp_path):
    from knowledge import common

    p = tmp_path / "AGENTS.md"
    common.upsert_managed_block(str(p), "only studio content")
    assert p.is_file()
    common.remove_managed_block(str(p))
    assert not p.exists()  # nothing user-owned remained


def test_merge_and_unmerge_json_mapping_preserve_siblings(tmp_path):
    from knowledge import common

    p = tmp_path / ".claude.json"
    p.write_text(json.dumps({"projects": {"x": 1}, "mcpServers": {"mine": {}}}))

    common.merge_json_mapping(str(p), "mcpServers", {"pegasus-docs": {"url": "u"}})
    data = json.loads(_read(str(p)))
    assert data["projects"] == {"x": 1}  # untouched
    assert set(data["mcpServers"]) == {"mine", "pegasus-docs"}

    common.unmerge_json_mapping(str(p), "mcpServers", ["pegasus-docs"])
    data = json.loads(_read(str(p)))
    assert set(data["mcpServers"]) == {"mine"}  # user's server kept
    assert data["projects"] == {"x": 1}


# --- claude_code adapter ----------------------------------------------------


def test_claude_code_writes_global_config(tmp_path, monkeypatch):
    from knowledge import antigravity, claude_code

    kroot = tmp_path / "knowledge"
    _seed_knowledge(str(kroot))
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(kroot))
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(kroot))

    home = tmp_path / "home"
    ws = home / "work"
    ws.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    claude_code.ClaudeCodeAdapter().install(
        str(ws),
        {"provider": "custom-x", "model": "default", "base_url": "http://gw/v1"},
    )

    # Global (loaded from ANY cwd) + workspace copies both exist.
    assert (home / ".claude" / "CLAUDE.md").is_file()
    assert (ws / "CLAUDE.md").is_file()
    global_settings = json.loads(_read(str(home / ".claude" / "settings.json")))
    assert global_settings["base_url"] == "http://gw/v1"
    assert global_settings["extraKnownMarketplaces"]["scitech"]["source"]["repo"]
    # MCP servers land in the user-scope ~/.claude.json.
    claude_json = json.loads(_read(str(home / ".claude.json")))
    assert "pegasus-docs" in claude_json["mcpServers"]


def test_claude_code_global_is_non_destructive(tmp_path, monkeypatch):
    from knowledge import claude_code

    kroot = tmp_path / "knowledge"
    _seed_knowledge(str(kroot))
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(kroot))

    home = tmp_path / "home"
    ws = home / "work"
    ws.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    # Pre-existing user global config that must survive.
    (home / ".claude").mkdir()
    (home / ".claude" / "CLAUDE.md").write_text("# user memory\n")
    (home / ".claude" / "settings.json").write_text(json.dumps({"theme": "dark"}))
    (home / ".claude.json").write_text(
        json.dumps({"projects": {"a": 1}, "mcpServers": {"mine": {}}})
    )

    adapter = claude_code.ClaudeCodeAdapter()
    adapter.install(str(ws), {"provider": "anthropic", "model": "m"})

    assert "user memory" in _read(str(home / ".claude" / "CLAUDE.md"))
    settings = json.loads(_read(str(home / ".claude" / "settings.json")))
    assert settings["theme"] == "dark"  # preserved
    assert settings["provider"] == "anthropic"  # ours added
    claude_json = json.loads(_read(str(home / ".claude.json")))
    assert claude_json["projects"] == {"a": 1}
    assert {"mine", "pegasus-docs"} <= set(claude_json["mcpServers"])

    # Uninstall removes only what we added.
    adapter.uninstall(str(ws))
    assert "user memory" in _read(str(home / ".claude" / "CLAUDE.md"))
    claude_json = json.loads(_read(str(home / ".claude.json")))
    assert set(claude_json["mcpServers"]) == {"mine"}
    settings = json.loads(_read(str(home / ".claude" / "settings.json")))
    assert settings["theme"] == "dark"
    assert "scitech" not in settings.get("extraKnownMarketplaces", {})


# --- antigravity adapter ----------------------------------------------------


def test_antigravity_writes_global_config(tmp_path, monkeypatch):
    from knowledge import antigravity

    kroot = tmp_path / "knowledge"
    _seed_knowledge(str(kroot))
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(kroot))

    home = tmp_path / "home"
    ws = home / "work"
    ws.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    # Pre-existing user setting must survive the merge.
    (home / ".antigravity").mkdir()
    (home / ".antigravity" / "settings.json").write_text(json.dumps({"keep": 1}))

    adapter = antigravity.AntigravityAdapter()
    adapter.install(
        str(ws), {"provider": "google", "model": "gemini", "base_url": "http://gw"}
    )

    assert (home / ".antigravity" / "context.md").is_file()
    settings = json.loads(_read(str(home / ".antigravity" / "settings.json")))
    assert settings["keep"] == 1  # preserved
    assert settings["provider"] == "google"
    assert settings["base_url"] == "http://gw"

    adapter.uninstall(str(ws))
    # Our keys gone, the user's key remains.
    settings = json.loads(_read(str(home / ".antigravity" / "settings.json")))
    assert settings == {"keep": 1}
    assert not (home / ".antigravity" / "context.md").exists()
