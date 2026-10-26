"""The workspace root, and what lives under it (#401).

The root moved from ``$HOME/work`` up to ``$HOME``. Two things follow, and both
are tested here rather than left to review: projects left in the old directory
are still listed, and the directories that only became reachable because the
root moved up — the Condor token, Studio's database, the user's keys — are not.
"""

from __future__ import annotations

import os

import pytest

import workspace as ws


# ── where the root is ─────────────────────────────────────────────────────


def test_root_is_home(monkeypatch):
    monkeypatch.delenv("STUDIO_WORKSPACE_DIR", raising=False)
    monkeypatch.setenv("HOME", "/home/alice")
    assert ws.workspace_root() == "/home/alice"
    assert ws.workflows_dir() == "/home/alice/workflows"
    assert ws.data_dir() == "/home/alice/data"


def test_explicit_override_still_wins(monkeypatch):
    """Open OnDemand hands Studio a directory that is not the home."""
    monkeypatch.setenv("HOME", "/home/alice")
    monkeypatch.setenv("STUDIO_WORKSPACE_DIR", "/scratch/alice/studio")
    assert ws.workspace_root() == "/scratch/alice/studio"
    assert ws.workflows_dir() == "/scratch/alice/studio/workflows"


def test_legacy_dir_is_only_reported_when_it_exists(tmp_path):
    assert ws.legacy_workflows_dirs(str(tmp_path)) == []
    legacy = tmp_path / "work" / "workflows"
    legacy.mkdir(parents=True)
    assert ws.legacy_workflows_dirs(str(tmp_path)) == [str(legacy)]


# ── protected paths ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "relative",
    [
        ".studio/studio.db",          # settings, chat history, LLM keys
        ".condor/tokens.d/studio",    # submit rights on the pool
        ".ssh/id_rsa",
        ".config/gh/hosts.yml",
        ".local/share/pegasus-ai",
        ".studio",                    # the directory itself, not only its files
    ],
)
def test_credential_dirs_are_protected(tmp_path, relative):
    root = os.path.realpath(str(tmp_path))
    assert ws.is_protected(os.path.join(root, relative), root)


@pytest.mark.parametrize(
    "relative",
    ["workflows/my-analysis/workflow_generator.py", "data/inputs.csv", ".gitignore"],
)
def test_the_user_s_own_files_are_not(tmp_path, relative):
    """Including dotfiles that are not one of the protected directories —
    a project's .pegasushub.yml and .gitignore are ordinary content."""
    root = os.path.realpath(str(tmp_path))
    assert not ws.is_protected(os.path.join(root, relative), root)


def test_a_prefix_match_is_not_a_containment_match(tmp_path):
    """`.sshfoo` is not inside `.ssh`."""
    root = os.path.realpath(str(tmp_path))
    assert not ws.is_protected(os.path.join(root, ".sshfoo"), root)


# ── legacy projects stay visible ──────────────────────────────────────────


def _project(parent, name):
    project = parent / name
    project.mkdir(parents=True)
    (project / "workflow_generator.py").write_text("pass\n")
    return str(project)


def test_projects_in_the_old_directory_are_still_listed(tmp_path, monkeypatch):
    """The whole migration: nothing is moved, the old directory is read.

    A user who had projects before the workspace collapsed into $HOME opens
    Studio and finds them where they left them.
    """
    from services import workflow_scanner as scanner

    current = _project(tmp_path / "workflows", "new-analysis")
    legacy = _project(tmp_path / "work" / "workflows", "old-analysis")

    monkeypatch.setattr(scanner, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(scanner, "WORKFLOWS_DIR", str(tmp_path / "workflows"))

    found = {p["project_id"]: p["project_dir"] for p in scanner.discover_projects()}
    assert found == {"new-analysis": current, "old-analysis": legacy}


def test_the_current_workspace_wins_a_name_collision(tmp_path, monkeypatch):
    from services import workflow_scanner as scanner

    current = _project(tmp_path / "workflows", "analysis")
    _project(tmp_path / "work" / "workflows", "analysis")

    monkeypatch.setattr(scanner, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(scanner, "WORKFLOWS_DIR", str(tmp_path / "workflows"))

    projects = scanner.discover_projects()
    assert [p["project_dir"] for p in projects] == [current]


def test_an_explicit_workspace_scans_only_that_directory(tmp_path, monkeypatch):
    """Callers that name a directory get it alone — no legacy dir folded in."""
    from services import workflow_scanner as scanner

    _project(tmp_path / "workflows", "new-analysis")
    _project(tmp_path / "work" / "workflows", "old-analysis")
    monkeypatch.setattr(scanner, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(scanner, "WORKFLOWS_DIR", str(tmp_path / "workflows"))

    projects = scanner.discover_projects(workspace=str(tmp_path / "workflows"))
    assert [p["project_id"] for p in projects] == ["new-analysis"]


def test_a_run_in_the_old_tree_still_resolves_to_its_project(tmp_path):
    """Outputs are found via the project's sites.yml, so a run that resolves to
    no project loses the difference between "nothing yet" and "we guessed"."""
    from services import run_outputs

    legacy_root = tmp_path / "work" / "workflows"
    project = _project(legacy_root, "old-analysis")
    run_dir = os.path.join(project, "submit", "run0001")
    os.makedirs(run_dir)

    roots = [str(tmp_path / "workflows"), str(legacy_root)]
    assert run_outputs.project_dir_for_run(run_dir, roots) == project
    assert run_outputs.project_dir_for_run(run_dir, roots[0]) is None


# ── the guards, through the API ───────────────────────────────────────────


def test_the_files_api_refuses_a_credential_directory(client):
    """The root is $HOME now, so "inside the workspace" stopped being enough."""
    root = client.workspace_root
    os.makedirs(os.path.join(root, ".studio"), exist_ok=True)
    with open(os.path.join(root, ".studio", "studio.db"), "w") as f:
        f.write("not really a database")

    res = client.get("/api/files", params={"path": os.path.join(root, ".studio")})
    assert res.status_code == 403

    res = client.get(
        "/api/files/read", params={"path": os.path.join(root, ".studio", "studio.db")}
    )
    assert res.status_code == 403


def test_the_chat_file_tools_refuse_one_too(monkeypatch, tmp_path):
    """Reading is denied as well as writing: an assistant that can read the
    Condor token can put it in a message."""
    from routers import chat

    root = os.path.realpath(str(tmp_path))
    monkeypatch.setattr(chat, "WORKSPACE_ROOT", root)
    os.makedirs(os.path.join(root, ".condor", "tokens.d"))

    with pytest.raises(ValueError, match="credentials"):
        chat._safe_path(".condor/tokens.d/studio")
    with pytest.raises(ValueError, match="credentials"):
        chat._safe_path(os.path.join(root, ".ssh", "id_rsa"))

    # Only the knowledge root is exempt from the protected .local tree.
    with pytest.raises(ValueError, match="credentials"):
        chat._safe_path(".local/share/opencode/auth.json")

    # Still the ordinary case, unchanged.
    assert chat._safe_path("workflows/a/workflow_generator.py") == os.path.join(
        root, "workflows", "a", "workflow_generator.py"
    )
    assert chat._safe_path(
        "workflows/a/workflow_generator.py", write=True
    ) == os.path.join(root, "workflows", "a", "workflow_generator.py")


def test_chat_file_tools_can_read_but_not_write_the_knowledge_tree(
    monkeypatch, tmp_path
):
    """Skills need their references and templates, but the checkout is not the
    user's workspace: reads are allowed, writes are not."""
    from routers import chat

    root = os.path.realpath(str(tmp_path / "home"))
    os.makedirs(root)
    monkeypatch.setattr(chat, "WORKSPACE_ROOT", root)

    knowledge = os.path.join(
        root, ".local", "share", "pegasus-ai", "marketplace",
        "plugins", "pegasus-ai",
    )
    os.makedirs(os.path.join(knowledge, "references"))
    guide = os.path.join(knowledge, "references", "PEGASUS.md")
    with open(guide, "w") as f:
        f.write("# guide\n")
    monkeypatch.setenv("KNOWLEDGE_ROOT", knowledge)

    assert chat._safe_path(guide) == guide
    with pytest.raises(ValueError, match="read-only"):
        chat._safe_path(guide, write=True)


def test_config_left_in_the_old_workspace_is_still_pruned(tmp_path, monkeypatch):
    """The prune target had to move with the workspace.

    It removes generated config that shadows the live copy, and it refuses to
    run when handed the config root — so once the workspace *became* $HOME,
    passing the workspace turned the cleanup into a no-op and left the stale
    $HOME/work copies in place for any shell that starts there.
    """
    from knowledge.common import prune_legacy_workspace_copies

    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = tmp_path / "work"
    legacy.mkdir()
    (legacy / "CLAUDE.md").write_text("stale\n")
    (tmp_path / "CLAUDE.md").write_text("live\n")

    removed = prune_legacy_workspace_copies(ws.legacy_workspace_root(str(tmp_path)))

    assert removed == ["CLAUDE.md"]
    assert not (legacy / "CLAUDE.md").exists()
    assert (tmp_path / "CLAUDE.md").read_text() == "live\n"


def test_pruning_never_runs_against_the_live_config(tmp_path, monkeypatch):
    from knowledge.common import prune_legacy_workspace_copies

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "CLAUDE.md").write_text("live\n")

    assert prune_legacy_workspace_copies(str(tmp_path)) == []
    assert (tmp_path / "CLAUDE.md").exists()
