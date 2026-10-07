"""Uninstall must remove what Studio generated and nothing else.

The adapters receive ``$HOME`` (``config_root()``), where ``.claude``,
``.codex``, ``.agents`` and ``.antigravity`` are the tools' real user state:
Claude Code credentials and transcripts, Codex auth and sessions, the user's
own skills and agents. Seeding that state and asserting it survives is the
point of every test here (NEW-01). The earlier test passed ``~/work`` as the
workspace, so the production shape — workspace == HOME — was never exercised.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest


def _checkout(tmp_path):
    """A minimal knowledge tree in the plugin layout (see test_knowledge.py)."""
    root = tmp_path / "checkout"
    (root / "skills" / "pegasus-scaffold").mkdir(parents=True)
    (root / "skills" / "pegasus-scaffold" / "SKILL.md").write_text(
        "---\nname: pegasus-scaffold\ndescription: d\n---\n"
    )
    (root / "agents").mkdir()
    (root / "agents" / "workflow-architect.md").write_text(
        "---\nname: workflow-architect\ndescription: d\n---\nbody\n"
    )
    (root / "references").mkdir()
    (root / "references" / "PEGASUS.md").write_text("# guide\n")
    # layout.mcp_servers_path() accepts either <root>/.mcp.json or the older
    # <root>/mcp/servers.json; the other fixtures in this suite use the latter.
    (root / "mcp").mkdir()
    (root / "mcp" / "servers.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "pegasus-docs": {
                        "type": "http",
                        "url": "https://gitmcp.io/pegasus-isi/pegasus",
                    }
                }
            }
        )
    )
    return root


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(_checkout(tmp_path)))
    # No checkout here: _marketplace_source() falls back to the GitHub source
    # under the legacy "scitech" name, which is also what uninstall must unmerge.
    monkeypatch.setenv("KNOWLEDGE_MARKETPLACE", str(tmp_path / "no-marketplace"))
    return h


# Relative path -> content. Every one of these is the user's or the tool's and
# must be byte-identical after any adapter's uninstall().
USER_FILES = {
    ".claude/.credentials.json": '{"claudeAiOauth": {"accessToken": "secret"}}',
    ".claude/projects/x/transcript.jsonl": '{"type": "user"}\n',
    ".claude/settings.local.json": '{"permissions": {"allow": ["Bash(ls)"]}}',
    ".codex/auth.json": '{"OPENAI_API_KEY": "sk-user"}',
    ".codex/sessions/2026/s.jsonl": "{}\n",
    ".codex/agents/my-agent.toml": 'name = "my-agent"\n',
    ".agents/notes.md": "my notes\n",
    ".antigravity/user.json": '{"k": 1}',
    ".config/opencode/agent/my-own.md": "mine\n",
    ".config/opencode/themes/t.json": "{}",
    ".config/crush/themes/t.json": "{}",
}


def _seed_user_state(home):
    for rel, content in USER_FILES.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    (home / ".claude" / "CLAUDE.md").write_text("# user memory\n")
    (home / ".claude" / "settings.json").write_text(json.dumps({"theme": "dark"}))
    (home / ".claude.json").write_text(
        json.dumps({"projects": {"/h": {}}, "mcpServers": {"mine": {"url": "u"}}})
    )


def _assert_user_state_intact(home):
    for rel, content in USER_FILES.items():
        assert (home / rel).read_text() == content, rel
    for d in (
        ".claude", ".codex", ".agents", ".antigravity", ".config/opencode",
        ".config/crush",
    ):
        assert (home / d).is_dir() and not (home / d).is_symlink(), d


def _json(path):
    return json.loads(path.read_text())


CFG = {"provider": "anthropic", "model": "m"}


# --- per-adapter: user state survives -----------------------------------------


def test_claude_code_uninstall_keeps_user_state(home):
    from knowledge import common
    from knowledge.claude_code import ClaudeCodeAdapter

    _seed_user_state(home)
    adapter = ClaudeCodeAdapter()
    adapter.install(str(home), CFG)
    # A CLAUDE.md the user wrote after install: startup wraps it in the block.
    (home / "CLAUDE.md").write_text("# mine\n")
    adapter.update_llm_config(str(home), CFG)
    assert common._BLOCK_BEGIN in (home / "CLAUDE.md").read_text()

    adapter.uninstall(str(home))

    _assert_user_state_intact(home)
    text = (home / "CLAUDE.md").read_text()
    assert text.strip() == "# mine"
    assert common._BLOCK_BEGIN not in text
    assert not (home / ".mcp.json").exists()
    memory = (home / ".claude" / "CLAUDE.md").read_text()
    assert memory.strip() == "# user memory"
    assert common._BLOCK_BEGIN not in memory
    claude_json = _json(home / ".claude.json")
    assert claude_json["mcpServers"] == {"mine": {"url": "u"}}
    assert claude_json["projects"] == {"/h": {}}
    assert _json(home / ".claude" / "settings.json") == {"theme": "dark"}


def test_codex_cli_uninstall_keeps_user_state(home):
    from knowledge.codex_cli import CodexCLIAdapter

    _seed_user_state(home)
    adapter = CodexCLIAdapter()
    adapter.install(str(home), {"provider": "openai", "model": "m"})
    assert (home / ".agents" / "skills").is_symlink()
    assert (home / ".codex" / "agents" / "workflow-architect.toml").is_file()

    adapter.uninstall(str(home))

    _assert_user_state_intact(home)
    assert not (home / "AGENTS.md").exists()
    assert not os.path.lexists(home / ".agents" / "skills")
    assert (home / ".agents" / "notes.md").read_text() == "my notes\n"
    assert not (home / ".codex" / "agents" / "workflow-architect.toml").exists()
    assert (home / ".codex" / "agents" / "my-agent.toml").is_file()
    assert not (home / ".codex" / "config.toml").exists()
    assert (home / ".codex" / "auth.json").is_file()


def test_codex_cli_uninstall_leaves_a_real_skills_directory_alone(home):
    from knowledge.codex_cli import CodexCLIAdapter

    skill = home / ".agents" / "skills" / "mine" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: mine\n---\n")

    CodexCLIAdapter().uninstall(str(home))

    assert skill.read_text() == "---\nname: mine\n---\n"
    assert (home / ".agents" / "skills").is_dir()
    assert not (home / ".agents" / "skills").is_symlink()


def test_codex_cli_keeps_a_user_config_toml_and_same_named_agent(home):
    """A user file under a name we also generate is theirs unless it carries
    what only our generator writes."""
    from knowledge.codex_cli import CodexCLIAdapter

    (home / ".codex" / "agents").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text('model = "o3"\n')
    (home / ".codex" / "agents" / "workflow-architect.toml").write_text(
        'name = "workflow-architect"\n'
    )

    CodexCLIAdapter().uninstall(str(home))

    assert (home / ".codex" / "config.toml").read_text() == 'model = "o3"\n'
    assert (home / ".codex" / "agents" / "workflow-architect.toml").read_text() == (
        'name = "workflow-architect"\n'
    )


def test_antigravity_uninstall_keeps_user_state(home):
    from knowledge.antigravity import AntigravityAdapter

    _seed_user_state(home)
    adapter = AntigravityAdapter()
    adapter.install(str(home), {"provider": "google", "model": "gemini"})
    # Written after install because install() overwrites settings.json
    # wholesale before merging (install-side defect, not fixed here).
    settings_path = home / ".antigravity" / "settings.json"
    settings = _json(settings_path)
    settings["keep"] = 1
    settings_path.write_text(json.dumps(settings))

    adapter.uninstall(str(home))

    _assert_user_state_intact(home)
    assert not (home / ".antigravity" / "context.md").exists()
    assert _json(settings_path) == {"keep": 1}
    assert (home / ".antigravity" / "user.json").read_text() == '{"k": 1}'


def test_opencode_uninstall_keeps_user_state(home):
    from knowledge.opencode import OpenCodeAdapter

    _seed_user_state(home)
    adapter = OpenCodeAdapter()
    adapter.install(str(home), {"provider": "gateway", "model": "m"})
    gdir = home / ".config" / "opencode"
    assert (gdir / "agent" / "workflow-architect.md").is_symlink()

    adapter.uninstall(str(home))

    _assert_user_state_intact(home)
    assert not (home / "AGENTS.md").exists()
    assert not (gdir / "AGENTS.md").exists()
    assert not (gdir / "opencode.json").exists()
    assert not os.path.lexists(gdir / "agent" / "workflow-architect.md")
    assert (gdir / "agent" / "my-own.md").read_text() == "mine\n"
    assert (gdir / "themes" / "t.json").read_text() == "{}"


def test_opencode_keeps_a_user_opencode_json(home):
    from knowledge.opencode import OpenCodeAdapter

    gdir = home / ".config" / "opencode"
    gdir.mkdir(parents=True)
    (gdir / "opencode.json").write_text(json.dumps({"theme": "x"}))

    OpenCodeAdapter().uninstall(str(home))

    assert _json(gdir / "opencode.json") == {"theme": "x"}


# --- across adapters -----------------------------------------------------------


def _adapters():
    from knowledge.antigravity import AntigravityAdapter
    from knowledge.claude_code import ClaudeCodeAdapter
    from knowledge.codex_cli import CodexCLIAdapter
    from knowledge.crush import CrushAdapter
    from knowledge.opencode import OpenCodeAdapter

    return {
        "claude_code": ClaudeCodeAdapter,
        "codex_cli": CodexCLIAdapter,
        "antigravity": AntigravityAdapter,
        "opencode": OpenCodeAdapter,
        "crush": CrushAdapter,
    }


def test_crush_uninstall_keeps_user_state(home):
    """Crush postdates the August fix and had kept the unconditional delete:
    ~/.config/crush/crush.json went whether Studio wrote it or the user did."""
    from knowledge.crush import CrushAdapter

    _seed_user_state(home)
    adapter = CrushAdapter()
    adapter.install(str(home), {"provider": "gateway", "model": "m"})
    gdir = home / ".config" / "crush"
    assert (gdir / "crush.json").is_file()
    assert (gdir / "AGENTS.md").is_file()

    adapter.uninstall(str(home))

    _assert_user_state_intact(home)
    assert not (home / "AGENTS.md").exists()
    assert not (gdir / "AGENTS.md").exists()
    assert not (gdir / "crush.json").exists()
    assert (gdir / "themes" / "t.json").read_text() == "{}"


def test_crush_keeps_a_user_crush_json(home):
    from knowledge.crush import CrushAdapter

    gdir = home / ".config" / "crush"
    gdir.mkdir(parents=True)
    (gdir / "crush.json").write_text(json.dumps({"providers": {"mine": {}}}))
    (gdir / "AGENTS.md").write_text("my own notes\n")

    CrushAdapter().uninstall(str(home))

    assert _json(gdir / "crush.json") == {"providers": {"mine": {}}}
    assert (gdir / "AGENTS.md").read_text() == "my own notes\n"


GATEWAY_CFG = {"provider": "gateway", "model": "m", "base_url": "http://gw/v1"}


@pytest.mark.parametrize(
    "name, rel",
    [
        ("opencode", ".config/opencode/opencode.json"),
        ("crush", ".config/crush/crush.json"),
        ("codex_cli", ".codex/config.toml"),
    ],
)
def test_uninstall_keeps_a_generated_config_the_user_edited(home, name, rel):
    """A fingerprint cannot tell "Studio wrote this" from "Studio wrote this
    and the user then added their own provider to it". The write is recorded
    by content hash, and uninstall removes the file only while it still
    matches — edited means it is the user's now."""
    adapter = _adapters()[name]()
    adapter.install(str(home), GATEWAY_CFG)
    path = home / rel
    assert path.is_file()

    if path.suffix == ".json":
        data = _json(path)
        data["my_change"] = True
        path.write_text(json.dumps(data, indent=2))
    else:
        path.write_text(path.read_text() + "\n# my_change\n")

    adapter.uninstall(str(home))

    assert path.is_file(), "an edited config must survive uninstall"
    assert "my_change" in path.read_text()


@pytest.mark.parametrize(
    "name, rel",
    [
        ("opencode", ".config/opencode/opencode.json"),
        ("crush", ".config/crush/crush.json"),
        ("codex_cli", ".codex/config.toml"),
    ],
)
def test_uninstall_removes_a_generated_config_left_as_written(home, name, rel):
    adapter = _adapters()[name]()
    adapter.install(str(home), GATEWAY_CFG)
    assert (home / rel).is_file()
    adapter.uninstall(str(home))
    assert not (home / rel).exists()
    # The manifest leaves with the last record.
    assert not (home / ".studio").exists()


def test_a_config_from_before_the_manifest_still_goes_by_fingerprint(home):
    """An install older than the manifest has no record; the content
    fingerprint is the only evidence left, and it still applies."""
    from knowledge.common import _generated_manifest_path
    from knowledge.opencode import OpenCodeAdapter

    adapter = OpenCodeAdapter()
    adapter.install(str(home), GATEWAY_CFG)
    os.remove(_generated_manifest_path())  # pretend the write predates it

    adapter.uninstall(str(home))
    assert not (home / ".config" / "opencode" / "opencode.json").exists()


@pytest.mark.parametrize(
    "name", ["claude_code", "codex_cli", "antigravity", "opencode", "crush"]
)
def test_uninstall_on_a_pristine_home_leaves_nothing_behind(home, name):
    adapter = _adapters()[name]()
    adapter.install(str(home), CFG)
    assert os.listdir(home)  # install did write something

    adapter.uninstall(str(home))

    # ~/.config is not ours to judge (XDG base dir); opencode only needs it empty.
    assert set(os.listdir(home)) <= {".config"}, os.listdir(home)
    if (home / ".config").exists():
        assert os.listdir(home / ".config") == []


@pytest.mark.parametrize(
    "name", ["claude_code", "codex_cli", "antigravity", "opencode", "crush"]
)
def test_uninstall_is_idempotent(home, name):
    adapter = _adapters()[name]()
    adapter.install(str(home), CFG)
    adapter.uninstall(str(home))
    adapter.uninstall(str(home))  # nothing left to remove; must not raise


def test_shared_agents_md_survives_as_user_file_when_hand_written(home):
    """codex_cli and opencode share ~/AGENTS.md; whichever goes first must
    leave the user's text for the other, and the other must leave it alone."""
    from knowledge import common
    from knowledge.codex_cli import CodexCLIAdapter
    from knowledge.opencode import OpenCodeAdapter

    (home / "AGENTS.md").write_text("mine\n")
    CodexCLIAdapter().update_llm_config(str(home), {"provider": "openai"})
    assert common._BLOCK_BEGIN in (home / "AGENTS.md").read_text()

    OpenCodeAdapter().uninstall(str(home))
    CodexCLIAdapter().uninstall(str(home))

    text = (home / "AGENTS.md").read_text()
    assert text.strip() == "mine"
    assert common._BLOCK_BEGIN not in text


# --- guards ----------------------------------------------------------------------


def test_no_adapter_or_installer_calls_rmtree():
    """Static guard: the adapters receive $HOME, so no recursive delete belongs
    in them. ``common.safe_rmtree`` is the only sanctioned primitive."""
    api_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in (
        os.path.join("knowledge", "claude_code.py"),
        os.path.join("knowledge", "codex_cli.py"),
        os.path.join("knowledge", "antigravity.py"),
        os.path.join("knowledge", "opencode.py"),
        os.path.join("knowledge", "crush.py"),
        os.path.join("services", "installer.py"),
    ):
        with open(os.path.join(api_dir, rel)) as f:
            assert "rmtree" not in f.read(), rel


def test_safe_rmtree_refuses_depth_one_dirs(home):
    from knowledge import common

    (home / ".claude" / "x").mkdir(parents=True)
    (home / ".config" / "opencode").mkdir(parents=True)

    assert common.safe_rmtree(str(home / ".claude")) is False
    assert (home / ".claude" / "x").is_dir()
    assert common.safe_rmtree(str(home)) is False
    assert home.is_dir()
    assert common.safe_rmtree(str(home / ".config" / "opencode")) is False
    assert (home / ".config" / "opencode").is_dir()
    assert common.is_protected_dir(str(home / ".config"))

    # Depth 2 under HOME is not a tool's root; that one is allowed.
    assert common.safe_rmtree(str(home / ".claude" / "x")) is True
    assert not (home / ".claude" / "x").exists()

    # The check follows realpath: a link elsewhere to ~/.claude is still ~/.claude.
    alias = home.parent / "alias"
    os.symlink(str(home / ".claude"), str(alias))
    assert common.is_protected_dir(str(alias))
    assert common.safe_rmtree(str(alias)) is False
    assert (home / ".claude").is_dir()


def test_remove_generated_context_file_rules(tmp_path, home):
    from knowledge import common, layout

    block_only = tmp_path / "a.md"
    common.upsert_managed_block(str(block_only), "x")
    assert common.remove_generated_context_file(str(block_only)) is True
    assert not block_only.exists()

    with_user = tmp_path / "b.md"
    with_user.write_text("# theirs\n")
    common.upsert_managed_block(str(with_user), "x")
    assert common.remove_generated_context_file(str(with_user)) is False
    assert with_user.read_text().strip() == "# theirs"
    assert common._BLOCK_BEGIN not in with_user.read_text()

    # install() writes the whole document with no markers.
    wholesale = tmp_path / "c.md"
    wholesale.write_text(common.context_body())
    assert common.remove_generated_context_file(str(wholesale)) is True
    assert not wholesale.exists()

    # The entrypoint writes overlay + guide with no appendix and no markers.
    entrypoint_shaped = tmp_path / "d.md"
    entrypoint_shaped.write_text(layout.context_document())
    assert common.remove_generated_context_file(str(entrypoint_shaped)) is True
    assert not entrypoint_shaped.exists()

    plain = tmp_path / "e.md"
    plain.write_text("just mine\n")
    assert common.remove_generated_context_file(str(plain)) is False
    assert plain.read_text() == "just mine\n"

    target = tmp_path / "target.md"
    common.upsert_managed_block(str(target), "x")
    link = tmp_path / "link.md"
    os.symlink(str(target), str(link))
    assert common.remove_generated_context_file(str(link)) is False
    assert link.is_symlink() and target.is_file()


# --- installer -----------------------------------------------------------------


class _FakeDB:
    def __init__(self, installed: set[str]):
        self.installed = installed
        self.removed: list[str] = []

    async def get_tool(self, tool_id):
        if tool_id in self.installed:
            return {"tool_id": tool_id, "status": "installed"}
        return None

    async def remove_tool(self, tool_id):
        self.removed.append(tool_id)


def _must_not_run(tool_id):
    raise AssertionError("adapter must not run")


def test_installer_uninstall_skips_adapter_when_not_installed(home, monkeypatch):
    from services import installer

    monkeypatch.setattr(installer, "get_adapter", _must_not_run)
    db = _FakeDB(installed=set())

    status = asyncio.run(installer.ToolInstaller().uninstall("claude-code", db))

    assert status == "not_installed"
    assert db.removed == []


def test_installer_uninstall_runs_adapter_when_installed(home, monkeypatch, tmp_path):
    from services import installer

    calls: list[str] = []

    class _Spy:
        def uninstall(self, workspace):
            calls.append(workspace)

    monkeypatch.setattr(installer, "get_adapter", lambda tool_id: _Spy())
    monkeypatch.setattr(installer, "WORKSPACE_ROOT", str(tmp_path / "work"))
    db = _FakeDB(installed={"claude-code"})

    status = asyncio.run(installer.ToolInstaller().uninstall("claude-code", db))

    assert status == "uninstalled"
    assert calls == [str(home)]
    assert db.removed == ["claude-code"]


def test_installer_uninstall_unknown_tool_raises(home):
    from services import installer

    with pytest.raises(ValueError):
        asyncio.run(installer.ToolInstaller().uninstall("nonexistent", _FakeDB(set())))


def test_uninstall_endpoint_is_a_no_op_for_an_uninstalled_tool(client, monkeypatch):
    from services import installer

    monkeypatch.setattr(installer, "get_adapter", _must_not_run)

    resp = client.post("/api/tools/claude-code/uninstall")
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_installed"

    resp = client.post("/api/tools/nonexistent/uninstall")
    assert resp.status_code == 404
