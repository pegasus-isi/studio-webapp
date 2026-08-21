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


def test_a_hand_written_context_file_survives(monkeypatch, tmp_path):
    """A file the user wrote is kept; the managed block goes above it."""
    from knowledge.common import ensure_context_file

    root = tmp_path / "checkout"
    (root / "references").mkdir(parents=True)
    (root / "references" / "PEGASUS.md").write_text("# Pegasus guide\n")
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))

    target = tmp_path / "work" / "AGENTS.md"
    target.parent.mkdir(parents=True)
    target.write_text("my own notes\n")

    assert ensure_context_file(str(target)) is True
    text = target.read_text()
    assert "my own notes" in text          # theirs, kept
    assert "Pegasus guide" in text         # ours, added above it


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
    """Otherwise the stale copy wins whenever cwd happens to be ~/work.

    A live pod was found with .opencode, .agents, .codex, .claude and
    .antigravity all still sitting in the workspace months after the config
    moved, quietly shadowing the current tree.
    """
    from knowledge.common import prune_legacy_workspace_copies

    home = tmp_path / "home"
    workspace = home / "work"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    (workspace / "CLAUDE.md").write_text("old")
    (workspace / "AGENTS.md").write_text("old")
    (workspace / "opencode.json").write_text("{}")
    (workspace / ".opencode" / "skills").mkdir(parents=True)
    (workspace / ".agents" / "skills").mkdir(parents=True)
    (workspace / ".codex" / "agents").mkdir(parents=True)
    (workspace / ".claude").mkdir()
    (workspace / ".claude" / "settings.json").write_text("{}")
    (workspace / ".antigravity").mkdir()
    (workspace / ".antigravity" / "context.md").write_text("old")
    keep = workspace / "my-workflow"
    keep.mkdir()

    removed = prune_legacy_workspace_copies(str(workspace))

    assert "CLAUDE.md" in removed and "opencode.json" in removed
    for parent in (".opencode", ".agents", ".codex", ".claude", ".antigravity"):
        assert not (workspace / parent).exists(), parent
    # Only generated names are touched — never the user's own work.
    assert keep.is_dir()


def test_pruning_keeps_a_directory_holding_the_users_own_files(monkeypatch, tmp_path):
    """Removing the parent wholesale would take a user's settings with it."""
    from knowledge.common import prune_legacy_workspace_copies

    home = tmp_path / "home"
    workspace = home / "work"
    (workspace / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    (workspace / ".claude" / "settings.json").write_text("{}")      # ours
    (workspace / ".claude" / "settings.local.json").write_text("!") # theirs

    prune_legacy_workspace_copies(str(workspace))

    assert not (workspace / ".claude" / "settings.json").exists()
    assert (workspace / ".claude" / "settings.local.json").read_text() == "!"


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


def test_a_stale_generated_file_is_refreshed(monkeypatch, tmp_path):
    """The bug this fixes: guidance shipped in the image never reached users.

    A generated file was written once and never touched again, so a pod kept
    last week's instructions no matter what the image said — indistinguishable
    from the fix never having been made.
    """
    from knowledge.common import ensure_context_file

    root = tmp_path / "checkout"
    (root / "references").mkdir(parents=True)
    (root / "references" / "PEGASUS.md").write_text("# NEW guidance\n")
    (root / "skills").mkdir()
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))
    monkeypatch.setenv("KNOWLEDGE_OVERLAY", str(tmp_path / "none"))

    target = tmp_path / "AGENTS.md"
    # What an older build left behind: no managed block, but recognisably ours.
    target.write_text("# OLD guidance\n\n## Available Pegasus Skills\n\n- /scaffold\n")

    assert ensure_context_file(str(target)) is True
    text = target.read_text()
    assert "NEW guidance" in text
    assert "OLD guidance" not in text


def test_refreshing_preserves_text_outside_the_block(monkeypatch, tmp_path):
    from knowledge.common import ensure_context_file

    root = tmp_path / "checkout"
    (root / "references").mkdir(parents=True)
    (root / "references" / "PEGASUS.md").write_text("# v1\n")
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))
    monkeypatch.setenv("KNOWLEDGE_OVERLAY", str(tmp_path / "none"))

    target = tmp_path / "CLAUDE.md"
    ensure_context_file(str(target))
    target.write_text(target.read_text() + "\n## My own notes\n\nkeep me\n")

    (root / "references" / "PEGASUS.md").write_text("# v2\n")
    assert ensure_context_file(str(target)) is True

    text = target.read_text()
    assert "v2" in text and "v1" not in text   # ours refreshed
    assert "keep me" in text                    # theirs untouched


def _fake_checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / "skills" / "pegasus-scaffold").mkdir(parents=True)
    (root / "skills" / "pegasus-scaffold" / "SKILL.md").write_text(
        "---\nname: pegasus-scaffold\ndescription: d\n---\n"
    )
    (root / "agents").mkdir()
    (root / "agents" / "workflow-architect.md").write_text(
        "---\nname: workflow-architect\ndescription: d\n---\n"
    )
    (root / "references").mkdir()
    (root / "references" / "PEGASUS.md").write_text("# guide\n")
    return root


def test_startup_declares_skills_by_absolute_path(monkeypatch, tmp_path):
    """Discovery must not depend on where the process started.

    opencode finds skills by walking up from the cwd. Studio's server starts in
    ~/work today, so that happened to work — but which skills the model can
    invoke should not be a property of a directory nobody chose deliberately.
    Declaring the checkout path settles it from any cwd.
    """
    import json

    from knowledge.opencode import OpenCodeAdapter

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    checkout = _fake_checkout(tmp_path)
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(checkout))

    OpenCodeAdapter().update_llm_config(str(home), {"provider": "gateway"})

    gdir = home / ".config" / "opencode"
    config = json.loads((gdir / "opencode.json").read_text())

    assert config["skills"] == {"paths": [str(checkout / "skills")]}
    # Points at the checkout itself, so a `git pull` is live with no relinking.
    assert (checkout / "skills" / "pegasus-scaffold" / "SKILL.md").is_file()

    # Same reasoning for the context file: a bare "AGENTS.md" resolves against
    # whatever project opencode walked up to.
    assert config["instructions"] == [str(gdir / "AGENTS.md")]
    assert (gdir / "AGENTS.md").is_file()


def test_startup_links_agents_into_the_global_dir(monkeypatch, tmp_path):
    """~/.config/opencode/agent/<name>.md is the documented any-cwd location."""
    from knowledge.opencode import OpenCodeAdapter

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(_fake_checkout(tmp_path)))

    OpenCodeAdapter().update_llm_config(str(home), {"provider": "gateway"})

    agent = home / ".config" / "opencode" / "agent" / "workflow-architect.md"
    assert agent.is_file()
    assert agent.is_symlink()

    # Nothing is left in the location opencode does not document.
    assert not (home / ".opencode").exists()


def test_agent_links_follow_the_checkout(monkeypatch, tmp_path):
    """An agent removed upstream must stop being offered; the user's own stays."""
    from knowledge.opencode import OpenCodeAdapter

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    checkout = _fake_checkout(tmp_path)
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(checkout))

    adapter = OpenCodeAdapter()
    adapter.update_llm_config(str(home), {"provider": "gateway"})

    agent_dir = home / ".config" / "opencode" / "agent"
    (agent_dir / "my-own.md").write_text("mine")          # hand-written
    (checkout / "agents" / "workflow-architect.md").unlink()

    adapter.update_llm_config(str(home), {"provider": "gateway"})

    assert not (agent_dir / "workflow-architect.md").exists()
    assert (agent_dir / "my-own.md").read_text() == "mine"


def test_codex_startup_creates_skill_links(monkeypatch, tmp_path):
    from knowledge.codex_cli import CodexCLIAdapter

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(_fake_checkout(tmp_path)))

    CodexCLIAdapter().update_llm_config(str(home), {"provider": "gateway"})

    assert (home / ".agents" / "skills" / "pegasus-scaffold" / "SKILL.md").is_file()


def test_slash_commands_resolve_against_installed_skills(monkeypatch, tmp_path):
    """The chat's skill table listed pre-plugin names, so every /command
    expanded to nothing — on the opencode path as much as the builtin one."""
    from routers.chat import _skill_for_command

    root = tmp_path / "checkout"
    for name in ("pegasus-scaffold", "pegasus-apptainer", "kiso"):
        (root / "skills" / name).mkdir(parents=True)
        (root / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))

    assert _skill_for_command("pegasus-scaffold") == "pegasus-scaffold"
    assert _skill_for_command("scaffold") == "pegasus-scaffold"   # old spelling
    assert _skill_for_command("kiso") == "kiso"                   # unprefixed skill
    assert _skill_for_command("dockerfile") == "pegasus-apptainer"  # renamed skill
    assert _skill_for_command("nonsense") is None


def test_chat_system_prompt_uses_the_resolved_tree(monkeypatch, tmp_path):
    """chat.py had its own KNOWLEDGE_ROOT pointing at the deleted tree, so the
    system prompt, agent personas and skills all silently went missing."""
    from routers.chat import _load_system_prompt

    root = tmp_path / "checkout"
    (root / "references").mkdir(parents=True)
    (root / "references" / "PEGASUS.md").write_text("# THE GUIDE\n")
    (root / "agents").mkdir()
    (root / "agents" / "workflow-architect.md").write_text(
        "---\nname: workflow-architect\n---\nPERSONA BODY\n"
    )
    (root / "skills").mkdir()
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))
    monkeypatch.setenv("KNOWLEDGE_OVERLAY", str(tmp_path / "none"))

    prompt = _load_system_prompt("workflow-architect")
    assert "THE GUIDE" in prompt
    assert "PERSONA BODY" in prompt
    # And it says not to emit a tool-call object, which is what went wrong.
    assert "no interactive question tool" in prompt


def test_declared_deliverables_reach_the_always_on_context(monkeypatch, tmp_path):
    """The obligation must not live only inside a skill the model may not load.

    Moving the list out of the overlay and into pegasus-scaffold was right for
    single-sourcing, but a weaker model never invoked the skill and finished
    workflows with no README.md or run_manual.sh. The skill still defines the
    list; the appendix renders it.
    """
    from knowledge.common import build_skills_appendix

    root = tmp_path / "checkout"
    (root / "skills" / "pegasus-scaffold").mkdir(parents=True)
    (root / "skills" / "pegasus-scaffold" / "SKILL.md").write_text(
        "---\n"
        "name: pegasus-scaffold\n"
        "description: Create a workflow\n"
        "deliverables:\n"
        "  - workflow_generator.py\n"
        "  - run_manual.sh\n"
        "  - README.md\n"
        "---\n\nbody\n"
    )
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))

    text = build_skills_appendix()
    assert "run_manual.sh" in text
    assert "README.md" in text
    assert "before you call it done" in text


def test_a_skill_without_deliverables_adds_nothing(monkeypatch, tmp_path):
    from knowledge.common import build_skills_appendix

    root = tmp_path / "checkout"
    (root / "skills" / "pegasus-debug").mkdir(parents=True)
    (root / "skills" / "pegasus-debug" / "SKILL.md").write_text(
        "---\nname: pegasus-debug\ndescription: Debug a run\n---\n"
    )
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))

    assert "must contain" not in build_skills_appendix()


def test_paths_appendix_states_the_absolute_root(monkeypatch, tmp_path):
    """The context must name the knowledge root, not leave it to be guessed.

    Skills say "read references/PEGASUS.md", which is relative to the plugin
    directory. Nothing resolved that at runtime: chat starts in the workspace and
    the tree is under ~/.local/share, so a pod log shows the model reaching for
    $HOME/references/PEGASUS.md and then scaffolding with no guide at all.
    """
    from knowledge.common import build_paths_appendix

    root = tmp_path / "checkout" / "plugins" / "pegasus-ai"
    (root / "references").mkdir(parents=True)
    (root / "references" / "PEGASUS.md").write_text("# guide\n")
    (root / "assets" / "templates").mkdir(parents=True)
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))

    out = build_paths_appendix()

    assert str(root) in out
    assert str(root / "references" / "PEGASUS.md") in out
    assert str(root / "assets" / "templates") in out
    # The point is not just the path but that a relative one must be resolved
    # against it; a bare path with no instruction is what already failed.
    assert "not** to your working directory" in out


def test_paths_appendix_empty_when_no_tree(monkeypatch, tmp_path):
    """No tree, no section. Naming a directory that is not there would send the
    model after a path guaranteed to fail, which is worse than saying nothing."""
    from knowledge.common import build_paths_appendix

    monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path / "absent"))
    assert build_paths_appendix() == ""


def test_paths_appendix_omits_subdirs_it_cannot_find(monkeypatch, tmp_path):
    """A tree without examples/ still gets a root and a guide, minus that line."""
    from knowledge.common import build_paths_appendix

    root = tmp_path / "pegasus-ai"
    (root / "references").mkdir(parents=True)
    (root / "references" / "PEGASUS.md").write_text("# guide\n")
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))

    out = build_paths_appendix()

    assert "Reference guide" in out
    assert "Worked examples" not in out


def test_knowledge_appendix_includes_the_paths_section(monkeypatch, tmp_path):
    """Wired into the combined appendix, so it reaches every generated context
    file (CLAUDE.md, AGENTS.md, .opencode) through one path."""
    from knowledge.common import build_knowledge_appendix

    root = tmp_path / "pegasus-ai"
    (root / "skills" / "pegasus-scaffold").mkdir(parents=True)
    (root / "skills" / "pegasus-scaffold" / "SKILL.md").write_text(
        "---\nname: pegasus-scaffold\ndescription: Scaffold a workflow\n---\n"
    )
    (root / "references").mkdir()
    (root / "references" / "PEGASUS.md").write_text("# guide\n")
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(root))

    out = build_knowledge_appendix()

    assert "## Where the Pegasus knowledge base lives" in out
    assert "## Available Pegasus Skills" in out
    # Paths first: the skill list tells the model what it can invoke, and every
    # one of those skills opens by reading a relative path.
    assert out.index("knowledge base lives") < out.index("Available Pegasus Skills")


def test_generated_context_with_paths_section_is_replaceable(monkeypatch, tmp_path):
    """A context file carrying only the paths heading is still recognised as ours.

    ensure_context_file refuses to overwrite a file it did not write. A build
    that emitted the paths section and nothing else would otherwise become
    permanently stale.
    """
    from knowledge.common import _looks_generated

    assert _looks_generated("## Where the Pegasus knowledge base lives\n")
