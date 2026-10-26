"""Tests for the builtin chat's generic file tools."""

from __future__ import annotations

import asyncio

import pytest

from routers import chat


def _exec(name, arguments):
    return asyncio.run(chat._execute_tool(name, arguments))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(chat, "WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path / "absent-knowledge"))
    return root


def _workflow(workspace, name="wf.py"):
    path = workspace / "workflows" / "a" / name
    path.parent.mkdir(parents=True)
    return path


def test_read_file_returns_requested_line_range(workspace):
    wf = _workflow(workspace)
    wf.write_text("\n".join(f"line{i}" for i in range(1, 101)) + "\n")

    out = _exec(
        "read_file",
        {"path": "workflows/a/wf.py", "offset": 11, "limit": 5},
    )

    assert "lines 11-15 of 100" in out
    assert "line11" in out
    assert "line15" in out
    assert "line10" not in out
    assert "line16" not in out
    assert "use offset=16" in out


def test_read_file_stays_below_tool_result_cap(workspace):
    wf = _workflow(workspace)
    wf.write_text("\n".join("x" * 200 for _ in range(200)) + "\n")

    out = _exec("read_file", {"path": "workflows/a/wf.py"})

    assert len(out) <= chat._MAX_TOOL_RESULT_CHARS
    assert "chunk capped" in out
    assert "use offset=" in out


def test_read_file_rejects_offset_past_eof(workspace):
    wf = _workflow(workspace)
    wf.write_text("one\ntwo\n")

    out = _exec(
        "read_file",
        {"path": "workflows/a/wf.py", "offset": 10},
    )

    assert "beyond end" in out
    assert "2 lines" in out


def test_edit_file_replaces_unique_text(workspace):
    wf = _workflow(workspace)
    wf.write_text("max_trees = 100\n")

    out = _exec(
        "edit_file",
        {
            "path": "workflows/a/wf.py",
            "old_string": "max_trees = 100",
            "new_string": "max_trees = 200",
        },
    )

    assert "Replaced 1" in out
    assert wf.read_text() == "max_trees = 200\n"


def test_edit_file_refuses_ambiguous_match(workspace):
    wf = _workflow(workspace)
    original = "x = 1\nx = 1\n"
    wf.write_text(original)

    out = _exec(
        "edit_file",
        {
            "path": "workflows/a/wf.py",
            "old_string": "x = 1",
            "new_string": "x = 2",
        },
    )

    assert "occurs 2 times" in out
    assert wf.read_text() == original


def test_edit_file_replace_all(workspace):
    wf = _workflow(workspace)
    wf.write_text("x = 1\nx = 1\n")

    out = _exec(
        "edit_file",
        {
            "path": "workflows/a/wf.py",
            "old_string": "x = 1",
            "new_string": "x = 2",
            "replace_all": True,
        },
    )

    assert "Replaced 2" in out
    assert wf.read_text() == "x = 2\nx = 2\n"


def test_edit_file_refuses_knowledge_write(workspace, monkeypatch):
    knowledge = (
        workspace
        / ".local"
        / "share"
        / "pegasus-ai"
        / "marketplace"
        / "plugins"
        / "pegasus-ai"
    )
    knowledge.mkdir(parents=True)
    guide = knowledge / "guide.md"
    guide.write_text("old\n")
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(knowledge))

    out = _exec(
        "edit_file",
        {
            "path": str(guide),
            "old_string": "old",
            "new_string": "new",
        },
    )

    assert "read-only" in out
    assert guide.read_text() == "old\n"


def test_search_finds_literal_matches(workspace):
    wf = _workflow(workspace)
    wf.write_text("other = 1\nmax_trees = 100\n")

    out = _exec("search", {"query": "max_trees", "path": "workflows"})

    assert "workflows/a/wf.py:2: max_trees = 100" in out


def test_search_supports_regex_and_case_sensitivity(workspace):
    wf = _workflow(workspace)
    wf.write_text("MAX_TREES = 7\n")

    regex_out = _exec(
        "search",
        {
            "query": r"max_trees\s*=\s*\d+",
            "path": "workflows",
            "regex": True,
        },
    )
    assert "MAX_TREES = 7" in regex_out

    exact_out = _exec(
        "search",
        {
            "query": "MAX_TREES",
            "path": "workflows",
            "case_sensitive": True,
        },
    )
    assert "MAX_TREES = 7" in exact_out

    wrong_case = _exec(
        "search",
        {
            "query": "max_trees",
            "path": "workflows",
            "case_sensitive": True,
        },
    )
    assert "No matches" in wrong_case


def test_search_caps_output(workspace):
    wf = _workflow(workspace)
    wf.write_text("\n".join(f"needle {i}" for i in range(20)) + "\n")

    out = _exec(
        "search",
        {"query": "needle", "path": "workflows", "max_results": 5},
    )

    assert "stopped after 5 matches" in out
    assert len(out) <= chat._MAX_TOOL_RESULT_CHARS


def test_search_skips_protected_workspace_dirs(workspace):
    secret = workspace / ".ssh" / "id_rsa"
    secret.parent.mkdir(parents=True)
    secret.write_text("needle\n")

    out = _exec("search", {"query": "needle", "path": ""})

    assert "No matches" in out


def test_search_can_search_the_knowledge_tree(workspace, monkeypatch):
    knowledge = workspace / "knowledge"
    knowledge.mkdir()
    guide = knowledge / "guide.md"
    guide.write_text("needle\n")
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(knowledge))

    out = _exec("search", {"query": "needle", "path": str(knowledge)})

    assert "needle" in out
    assert "guide.md:1" in out


def test_file_tools_are_registered_and_ungated():
    names = {t["function"]["name"] for t in chat.TOOL_DEFINITIONS}

    for name in ("search", "edit_file", "read_file"):
        assert name in names
    for name in ("search", "edit_file"):
        assert name not in chat._APPROVAL_REQUIRED_TOOLS
