"""Tests for knowledge layer endpoints."""

from __future__ import annotations

import json
import os


def test_list_skills_empty(client):
    resp = client.get("/api/knowledge/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert "skills" in data
    assert isinstance(data["skills"], list)


def test_list_skills_with_data(client, monkeypatch):
    from knowledge import layout

    knowledge_root = layout.knowledge_root()

    # Create a test skill
    skill_dir = os.path.join(knowledge_root, "skills", "scaffold")
    os.makedirs(skill_dir, exist_ok=True)
    with open(os.path.join(skill_dir, "metadata.json"), "w") as f:
        json.dump(
            {"name": "scaffold", "description": "Scaffold a workflow", "slash_command": "/scaffold"},
            f,
        )
    with open(os.path.join(skill_dir, "canonical.md"), "w") as f:
        f.write("# Scaffold Skill\nCreate a new Pegasus workflow.")

    resp = client.get("/api/knowledge/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["skills"]) >= 1
    assert data["skills"][0]["name"] == "scaffold"


def test_get_skill(client):
    from knowledge import layout

    knowledge_root = layout.knowledge_root()

    skill_dir = os.path.join(knowledge_root, "skills", "test-skill")
    os.makedirs(skill_dir, exist_ok=True)
    with open(os.path.join(skill_dir, "canonical.md"), "w") as f:
        f.write("# Test Skill Content")
    with open(os.path.join(skill_dir, "metadata.json"), "w") as f:
        json.dump({"name": "test-skill", "description": "A test skill"}, f)

    resp = client.get("/api/knowledge/skills/test-skill")
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "# Test Skill Content"


def test_get_skill_not_found(client):
    resp = client.get("/api/knowledge/skills/nonexistent")
    assert resp.status_code == 404


def test_list_agents(client):
    resp = client.get("/api/knowledge/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert "agents" in data
    # Should always have the "general" agent
    ids = [a["id"] for a in data["agents"]]
    assert "general" in ids


def test_list_templates_empty(client):
    resp = client.get("/api/knowledge/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert "templates" in data


def test_list_examples_empty(client):
    resp = client.get("/api/knowledge/examples")
    assert resp.status_code == 200
    data = resp.json()
    assert "examples" in data


def test_skill_metadata_comes_from_frontmatter(client):
    """A skill in the plugin layout is described by its own frontmatter.

    metadata.json no longer exists upstream, so the description has to come from
    the document itself -- which also means it cannot drift from it.
    """
    from knowledge import layout

    skill_dir = os.path.join(layout.knowledge_root(), "skills", "pegasus-scaffold")
    os.makedirs(skill_dir, exist_ok=True)
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write(
            "---\n"
            "name: pegasus-scaffold\n"
            "description: Create a complete Pegasus workflow project\n"
            "order: 1\n"
            "allowed-tools:\n"
            "  - Read\n"
            "---\n\n# Scaffold\n"
        )

    resp = client.get("/api/knowledge/skills/pegasus-scaffold")
    assert resp.status_code == 200
    meta = resp.json()["metadata"]
    assert meta["description"] == "Create a complete Pegasus workflow project"
    assert meta["slash_command"] == "/pegasus-scaffold"

    listing = client.get("/api/knowledge/skills").json()["skills"]
    # order: 1 sorts it ahead of anything unordered.
    assert listing[0]["slash_command"] == "/pegasus-scaffold"


def test_folded_description_is_joined(tmp_path):
    """kiso's description is a YAML folded scalar spanning several lines."""
    from knowledge import layout

    doc = tmp_path / "SKILL.md"
    doc.write_text(
        "---\n"
        "name: kiso\n"
        "description: >\n"
        "  Generate a Kiso experiment.yml configuration file\n"
        "  for running Pegasus workflows.\n"
        "allowed-tools:\n"
        "  - Read\n"
        "---\n\nbody\n"
    )
    front = layout.parse_frontmatter(str(doc))
    assert front["name"] == "kiso"
    assert front["description"] == (
        "Generate a Kiso experiment.yml configuration file "
        "for running Pegasus workflows."
    )


def test_knowledge_root_resolution_order(monkeypatch, tmp_path):
    """Env wins over the installed package, which wins over the legacy path.

    Resolved per call, not cached at import: tests and the container set this
    differently, and an import-time constant would freeze whichever ran first.
    """
    from knowledge import layout

    monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path))
    assert layout.knowledge_root() == str(tmp_path)

    monkeypatch.delenv("KNOWLEDGE_ROOT", raising=False)
    monkeypatch.setenv("KNOWLEDGE_MARKETPLACE", str(tmp_path / "absent"))
    assert layout.knowledge_root() == "/opt/pegasus-ai/knowledge"

    # The image sets KNOWLEDGE_ROOT, but a checkout is found without it too.
    checkout = tmp_path / "marketplace"
    (checkout / "plugins" / "pegasus-ai").mkdir(parents=True)
    monkeypatch.setenv("KNOWLEDGE_MARKETPLACE", str(checkout))
    assert layout.knowledge_root() == str(checkout / "plugins" / "pegasus-ai")


def test_templates_prefers_plugin_assets_layout(monkeypatch, tmp_path):
    """assets/templates (plugin layout) wins over templates/ (old Studio layout)."""
    from knowledge import layout

    monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path))
    (tmp_path / "templates").mkdir()
    assert layout.templates_dir() == str(tmp_path / "templates")

    (tmp_path / "assets" / "templates").mkdir(parents=True)
    assert layout.templates_dir() == str(tmp_path / "assets" / "templates")


def test_marketplace_endpoints(client, monkeypatch, tmp_path):
    """The UI needs the SHA and a way to update; both must survive a
    non-git tree rather than 500."""
    plain = tmp_path / "mkt"
    (plain / ".claude-plugin").mkdir(parents=True)
    monkeypatch.setenv("KNOWLEDGE_MARKETPLACE", str(plain))

    body = client.get("/api/knowledge/marketplace").json()
    assert body["path"] == str(plain)
    assert body["exists"] is True
    assert body["is_git"] is False

    body = client.post("/api/knowledge/marketplace/update").json()
    assert body["updated"] is False
    assert body["reason"] == "not a git checkout"


def test_tool_skill_dirs_are_symlinks_not_copies(monkeypatch, tmp_path):
    """One copy on disk, so a checkout update is visible to every tool at once.

    Copies were found going stale per user: a live pod carried three different
    versions of the same skills, including one naming a skill that had been
    withdrawn.
    """
    from knowledge.common import link_skills_to_dir

    root = tmp_path / "checkout"
    (root / "skills" / "pegasus-scaffold").mkdir(parents=True)
    (root / "skills" / "pegasus-scaffold" / "SKILL.md").write_text(
        "---\nname: pegasus-scaffold\ndescription: d\n---\n"
    )
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))

    dst = tmp_path / "work" / ".opencode" / "skills"
    assert link_skills_to_dir(str(dst)) is True
    assert os.path.islink(dst)
    assert (dst / "pegasus-scaffold" / "SKILL.md").is_file()

    # A skill added upstream appears immediately — the point of the symlink.
    (root / "skills" / "pegasus-debug").mkdir()
    (root / "skills" / "pegasus-debug" / "SKILL.md").write_text("---\nname: x\n---\n")
    assert (dst / "pegasus-debug" / "SKILL.md").is_file()


def test_a_stale_copy_is_replaced(monkeypatch, tmp_path):
    """Existing workspaces carry real directories of outdated skills."""
    from knowledge.common import link_skills_to_dir

    root = tmp_path / "checkout"
    (root / "skills" / "pegasus-apptainer").mkdir(parents=True)
    (root / "skills" / "pegasus-apptainer" / "SKILL.md").write_text("---\nname: a\n---\n")
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))

    # The stale copy a pod was found with: old names, and a withdrawn skill.
    dst = tmp_path / "work" / ".opencode" / "skills"
    (dst / "dockerfile").mkdir(parents=True)
    (dst / "provd").mkdir()

    assert link_skills_to_dir(str(dst)) is True
    assert os.path.islink(dst)
    assert not (dst / "provd").exists()
    assert (dst / "pegasus-apptainer").is_dir()


def test_relinking_is_idempotent(monkeypatch, tmp_path):
    from knowledge.common import link_skills_to_dir

    root = tmp_path / "checkout"
    (root / "skills").mkdir(parents=True)
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))
    dst = tmp_path / ".opencode" / "skills"

    assert link_skills_to_dir(str(dst)) is True
    assert link_skills_to_dir(str(dst)) is True
    assert os.path.realpath(dst) == os.path.realpath(root / "skills")


def test_context_files_are_restored_when_missing(monkeypatch, tmp_path):
    """A workspace that lost CLAUDE.md/AGENTS.md never got them back.

    They are written by install(), but startup only runs update_llm_config(),
    so a user who cleared their workspace ran every tool with no Pegasus
    context until they reinstalled something from Settings.
    """
    from knowledge.common import ensure_context_file

    root = tmp_path / "checkout"
    (root / "references").mkdir(parents=True)
    (root / "references" / "PEGASUS.md").write_text("# Pegasus guide\n")
    (root / "skills").mkdir()
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))
    monkeypatch.setenv("KNOWLEDGE_OVERLAY", str(tmp_path / "no-overlay"))

    target = tmp_path / "work" / "AGENTS.md"
    assert ensure_context_file(str(target)) is True
    assert "Pegasus guide" in target.read_text()


def test_context_file_never_overwrites_user_edits(monkeypatch, tmp_path):
    from knowledge.common import ensure_context_file

    root = tmp_path / "checkout"
    (root / "references").mkdir(parents=True)
    (root / "references" / "PEGASUS.md").write_text("# Pegasus guide\n")
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))

    target = tmp_path / "work" / "AGENTS.md"
    target.parent.mkdir(parents=True)
    target.write_text("my own notes\n")

    assert ensure_context_file(str(target)) is False
    assert target.read_text() == "my own notes\n"


def test_update_llm_config_restores_claude_md(monkeypatch, tmp_path):
    """The startup path, end to end: config propagation heals the workspace."""
    from knowledge.claude_code import ClaudeCodeAdapter

    root = tmp_path / "checkout"
    (root / "references").mkdir(parents=True)
    (root / "references" / "PEGASUS.md").write_text("# Pegasus guide\n")
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    workspace = tmp_path / "home" / "work"
    workspace.mkdir(parents=True)

    ClaudeCodeAdapter().update_llm_config(str(workspace), {"provider": "anthropic"})
    assert (workspace / "CLAUDE.md").is_file()


def test_config_root_is_home_not_the_workspace(monkeypatch, tmp_path):
    """Tools look in the directory they start in, upwards — and that is $HOME.

    Writing to $HOME/work produced files that existed but were never found: a
    terminal opens at ~, so nothing below it is on the search path.
    """
    from knowledge.common import config_root

    monkeypatch.setenv("HOME", str(tmp_path))
    assert config_root() == str(tmp_path)


def test_legacy_workspace_copies_are_pruned(monkeypatch, tmp_path):
    """Otherwise the stale copy wins whenever cwd happens to be ~/work."""
    from knowledge.common import prune_legacy_workspace_copies

    home = tmp_path / "home"
    workspace = home / "work"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    (workspace / "CLAUDE.md").write_text("old")
    (workspace / "AGENTS.md").write_text("old")
    (workspace / "opencode.json").write_text("{}")
    (workspace / ".opencode" / "skills").mkdir(parents=True)
    keep = workspace / "my-workflow"
    keep.mkdir()

    removed = prune_legacy_workspace_copies(str(workspace))

    assert set(removed) >= {"CLAUDE.md", "AGENTS.md", "opencode.json", ".opencode"}
    assert not (workspace / "CLAUDE.md").exists()
    assert not (workspace / ".opencode").exists()
    # Only generated names are touched — never the user's own work.
    assert keep.is_dir()


def test_pruning_never_deletes_the_live_files(monkeypatch, tmp_path):
    """If the workspace *is* the config root, pruning must be a no-op."""
    from knowledge.common import prune_legacy_workspace_copies

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "CLAUDE.md").write_text("live")

    assert prune_legacy_workspace_copies(str(tmp_path)) == []
    assert (tmp_path / "CLAUDE.md").read_text() == "live"


def test_state_dir_is_home_not_the_workspace(monkeypatch, tmp_path):
    """The DB is Studio's state, not the user's work.

    Living at ~/work/.studio put settings, LLM configuration and history inside
    the directory users are told to work in, where a `rm -rf` could take them.
    """
    import db as db_mod

    monkeypatch.setenv("HOME", str(tmp_path))
    assert db_mod._state_dir() == str(tmp_path / ".studio")


def test_legacy_database_is_moved_not_abandoned(monkeypatch, tmp_path):
    """Repointing alone would be indistinguishable from data loss."""
    import db as db_mod

    legacy_dir = tmp_path / "work" / ".studio"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "studio.db").write_text("PRETEND SQLITE")
    (legacy_dir / "studio.db-wal").write_text("wal")
    monkeypatch.setattr(db_mod, "_LEGACY_DB_PATH", str(legacy_dir / "studio.db"))

    new_path = tmp_path / ".studio" / "studio.db"
    assert db_mod._migrate_legacy_db(str(new_path)) is True
    assert new_path.read_text() == "PRETEND SQLITE"
    # The write-ahead log follows, so a stale one cannot be applied later.
    assert (tmp_path / ".studio" / "studio.db-wal").is_file()
    assert not (legacy_dir / "studio.db").exists()

    # Idempotent: a later start must never clobber the live database.
    (legacy_dir / "studio.db").write_text("STALE")
    assert db_mod._migrate_legacy_db(str(new_path)) is False
    assert new_path.read_text() == "PRETEND SQLITE"
