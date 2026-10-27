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
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _demo_project(workspace):
    root = workspace / "workflows" / "demo"
    (root / "bin").mkdir(parents=True)
    (root / "config.yml").write_text("some_name: 500\n")
    (root / "workflow_generator.py").write_text(
        'args = ["--some-name", str(cfg["some_name"])]\n'
    )
    (root / "bin" / "wrapper.py").write_text(
        'p.add_argument("--some-name", type=int)\n'
        'value = args.some_name\n'
    )
    (root / "run_manual.sh").write_text(
        "python bin/wrapper.py --some-name 500\n"
    )
    return root


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


def test_edit_blocks_renames_parameter_across_layers(workspace):
    root = _demo_project(workspace)

    out = _exec("edit_blocks", {"edits": [
        {
            "path": "workflows/demo/config.yml",
            "old_string": "some_name: 500",
            "new_string": "other_name: 500",
        },
        {
            "path": "workflows/demo/workflow_generator.py",
            "old_string": '["--some-name", str(cfg["some_name"])]',
            "new_string": '["--other-name", str(cfg["other_name"])]',
        },
        {
            "path": "workflows/demo/bin/wrapper.py",
            "old_string": (
                'p.add_argument("--some-name", type=int)\n'
                "value = args.some_name"
            ),
            "new_string": (
                'p.add_argument("--other-name", type=int)\n'
                "value = args.other_name"
            ),
        },
        {
            "path": "workflows/demo/run_manual.sh",
            "old_string": "--some-name 500",
            "new_string": "--other-name 500",
        },
    ]})

    assert "Edited 4 file(s) (4 block(s))" in out
    assert (root / "config.yml").read_text() == "other_name: 500\n"
    assert 'str(cfg["other_name"])' in (
        root / "workflow_generator.py"
    ).read_text()
    assert "args.other_name" in (root / "bin" / "wrapper.py").read_text()
    assert "--other-name 500" in (root / "run_manual.sh").read_text()

    old = _exec("search", {
        "query": "some[-_]name",
        "path": "workflows/demo",
        "regex": True,
    })
    assert "No matches" in old


def test_edit_blocks_does_not_conflate_separators(workspace):
    wf = _workflow(workspace)
    wf.write_text("some_name = 1\n--some-name = 2\n")

    out = _exec("edit_blocks", {"edits": [{
        "path": "workflows/a/wf.py",
        "old_string": "some_name = 1",
        "new_string": "other_name = 1",
    }]})

    assert "Edited 1 file(s)" in out
    text = wf.read_text()
    assert "other_name = 1" in text
    assert "--some-name = 2" in text


def test_edit_blocks_is_atomic(workspace):
    wf = _workflow(workspace)
    wf.write_text("keep\n")

    out = _exec("edit_blocks", {"edits": [
        {
            "path": "workflows/a/wf.py",
            "old_string": "keep",
            "new_string": "changed",
        },
        {
            "path": "workflows/a/wf.py",
            "old_string": "missing",
            "new_string": "x",
        },
    ]})

    assert out.startswith("Error:")
    assert wf.read_text() == "keep\n"


def test_edit_blocks_rejects_ambiguous_block(workspace):
    wf = _workflow(workspace)
    wf.write_text("x = 1\nx = 1\n")

    out = _exec("edit_blocks", {"edits": [{
        "path": "workflows/a/wf.py",
        "old_string": "x = 1",
        "new_string": "x = 2",
    }]})

    assert "occurs 2 times" in out
    assert "lines 1, 2" in out
    assert wf.read_text() == "x = 1\nx = 1\n"


def test_edit_blocks_applies_edits_in_order(workspace):
    wf = _workflow(workspace)
    wf.write_text("alpha\n")

    out = _exec("edit_blocks", {"edits": [
        {
            "path": "workflows/a/wf.py",
            "old_string": "alpha",
            "new_string": "beta",
        },
        {
            "path": "workflows/a/wf.py",
            "old_string": "beta",
            "new_string": "gamma",
        },
    ]})

    assert "Edited 1 file(s) (2 block(s))" in out
    assert wf.read_text() == "gamma\n"


def test_edit_blocks_replace_all(workspace):
    wf = _workflow(workspace)
    wf.write_text("x = 1\nx = 1\n")

    out = _exec("edit_blocks", {"edits": [{
        "path": "workflows/a/wf.py",
        "old_string": "x = 1",
        "new_string": "x = 2",
        "replace_all": True,
    }]})

    assert "Edited 1 file(s)" in out
    assert wf.read_text() == "x = 2\nx = 2\n"


def test_edit_blocks_matches_unicode_quotes(workspace):
    wf = _workflow(workspace)
    wf.write_text('name = “value”\n')

    out = _exec("edit_blocks", {"edits": [{
        "path": "workflows/a/wf.py",
        "old_string": 'name = "value"\n',
        "new_string": 'name = "new"\n',
    }]})

    assert "Edited 1 file(s)" in out
    assert wf.read_text() == 'name = "new"\n'


def test_edit_blocks_ignores_trailing_whitespace(workspace):
    wf = _workflow(workspace)
    wf.write_text("alpha   \nbeta   \n")

    out = _exec("edit_blocks", {"edits": [{
        "path": "workflows/a/wf.py",
        "old_string": "alpha\nbeta\n",
        "new_string": "alpha\ngamma\n",
    }]})

    assert "Edited 1 file(s)" in out
    assert wf.read_text() == "alpha\ngamma\n"


def test_edit_blocks_preserves_crlf(workspace):
    wf = _workflow(workspace)
    wf.write_bytes(b"a\r\nb\r\n")

    out = _exec("edit_blocks", {"edits": [{
        "path": "workflows/a/wf.py",
        "old_string": "a\nb\n",
        "new_string": "a\nc\n",
    }]})

    assert "Edited 1 file(s)" in out
    assert wf.read_bytes() == b"a\r\nc\r\n"


def test_edit_blocks_preserves_bom(workspace):
    wf = _workflow(workspace)
    wf.write_bytes(b"\xef\xbb\xbfold\n")

    out = _exec("edit_blocks", {"edits": [{
        "path": "workflows/a/wf.py",
        "old_string": "old",
        "new_string": "new",
    }]})

    assert "Edited 1 file(s)" in out
    data = wf.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert data.endswith(b"new\n")


def test_edit_blocks_rejects_missing_file(workspace):
    out = _exec("edit_blocks", {"edits": [{
        "path": "workflows/a/missing.py",
        "old_string": "a",
        "new_string": "b",
    }]})

    assert "not an existing file" in out
    assert "write_file" in out


def test_edit_blocks_rejects_binary_file(workspace):
    wf = _workflow(workspace)
    wf.write_bytes(b"\x00abc")

    out = _exec("edit_blocks", {"edits": [{
        "path": "workflows/a/wf.py",
        "old_string": "abc",
        "new_string": "def",
    }]})

    assert "not a text file" in out


def test_edit_blocks_rejects_empty_edits(workspace):
    out = _exec("edit_blocks", {"edits": []})
    assert "non-empty array" in out


def test_edit_blocks_rejects_too_many_edits(workspace):
    out = _exec("edit_blocks", {
        "edits": [
            {"path": "x", "old_string": "a", "new_string": "b"}
            for _ in range(21)
        ],
    })
    assert "at most 20" in out


def test_edit_blocks_rejects_identical_strings(workspace):
    wf = _workflow(workspace)
    wf.write_text("old\n")

    out = _exec("edit_blocks", {"edits": [{
        "path": "workflows/a/wf.py",
        "old_string": "old",
        "new_string": "old",
    }]})

    assert "identical" in out
    assert wf.read_text() == "old\n"


def test_edit_blocks_refuses_knowledge_write(workspace, monkeypatch):
    knowledge = workspace / "knowledge"
    knowledge.mkdir()
    guide = knowledge / "guide.md"
    guide.write_text("old\n")
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(knowledge))

    out = _exec("edit_blocks", {"edits": [{
        "path": str(guide),
        "old_string": "old",
        "new_string": "new",
    }]})

    assert "read-only" in out
    assert guide.read_text() == "old\n"


def test_edit_blocks_refuses_protected_dir(workspace):
    secret = workspace / ".ssh" / "id_rsa"
    secret.parent.mkdir()
    secret.write_text("old\n")

    out = _exec("edit_blocks", {"edits": [{
        "path": ".ssh/id_rsa",
        "old_string": "old",
        "new_string": "new",
    }]})

    assert "credentials" in out
    assert secret.read_text() == "old\n"


def test_write_file_requires_overwrite_for_large_existing(workspace):
    wf = _workflow(workspace, "large.py")
    original = "x" * (chat._MAX_TOOL_RESULT_CHARS + 1)
    wf.write_text(original)

    out = _exec("write_file", {
        "path": "workflows/a/large.py",
        "content": "new\n",
    })
    assert "larger than" in out
    assert "overwrite: true" in out
    assert wf.read_text() == original

    out = _exec("write_file", {
        "path": "workflows/a/large.py",
        "content": "new\n",
        "overwrite": True,
    })
    assert "Wrote" in out
    assert wf.read_text() == "new\n"


def test_write_file_allows_small_existing_without_overwrite(workspace):
    wf = _workflow(workspace, "small.py")
    wf.write_text("old\n")

    out = _exec("write_file", {
        "path": "workflows/a/small.py",
        "content": "new\n",
    })
    assert "Wrote" in out
    assert wf.read_text() == "new\n"


def test_write_file_allows_new_file_without_overwrite(workspace):
    out = _exec("write_file", {
        "path": "workflows/a/new.py",
        "content": "new\n",
    })
    assert "Wrote" in out
    assert (workspace / "workflows" / "a" / "new.py").read_text() == "new\n"


def test_write_file_rejects_directory(workspace):
    directory = workspace / "workflows" / "a" / "adir"
    directory.mkdir(parents=True)

    out = _exec("write_file", {
        "path": "workflows/a/adir",
        "content": "new\n",
    })
    assert "not a file" in out


def test_write_file_allows_exactly_16k(workspace):
    wf = _workflow(workspace, "edge.py")
    wf.write_text("x" * chat._MAX_TOOL_RESULT_CHARS)

    out = _exec("write_file", {
        "path": "workflows/a/edge.py",
        "content": "new\n",
    })
    assert "Wrote" in out


def test_search_requires_regex_for_separator_variants(workspace):
    a = _workflow(workspace, "a.py")
    b = _workflow(workspace, "b.py")
    a.write_text("some_name = 1\n")
    b.write_text("--some-name = 2\n")

    literal = _exec("search", {
        "query": "some_name",
        "path": "workflows",
    })
    assert "a.py" in literal
    assert "b.py" not in literal

    regex = _exec("search", {
        "query": "some[-_]name",
        "path": "workflows",
        "regex": True,
    })
    assert "a.py" in regex
    assert "b.py" in regex


def test_search_reports_counts_and_files_only(workspace):
    a = _workflow(workspace, "a.py")
    b = _workflow(workspace, "b.py")
    a.write_text("needle\n")
    b.write_text("needle\n")

    out = _exec("search", {"query": "needle", "path": "workflows"})
    assert "2 match(es) in 2 file(s):" in out

    out = _exec("search", {
        "query": "needle",
        "path": "workflows",
        "files_only": True,
    })
    assert set(out.splitlines()) == {
        "workflows/a/a.py",
        "workflows/a/b.py",
    }


def test_search_skips_ipynb_checkpoints(workspace):
    real = _workflow(workspace, "a.py")
    real.write_text("needle\n")
    checkpoint = workspace / ".ipynb_checkpoints" / "old.py"
    checkpoint.parent.mkdir()
    checkpoint.write_text("needle\n")

    out = _exec("search", {"query": "needle", "path": ""})
    assert "a.py" in out
    assert ".ipynb_checkpoints" not in out


def test_read_file_line_numbers_optional(workspace):
    wf = _workflow(workspace)
    wf.write_text("one\ntwo\n")

    with_numbers = _exec("read_file", {
        "path": "workflows/a/wf.py",
        "line_numbers": True,
    })
    assert "1: one" in with_numbers
    assert "2: two" in with_numbers

    without = _exec("read_file", {"path": "workflows/a/wf.py"})
    assert "1: one" not in without


def test_file_tools_are_registered_and_ungated():
    names = {t["function"]["name"] for t in chat.TOOL_DEFINITIONS}

    for name in (
        "search", "edit_file", "edit_blocks", "read_file", "write_file",
    ):
        assert name in names
    for name in ("search", "edit_file", "edit_blocks", "write_file"):
        assert name not in chat._APPROVAL_REQUIRED_TOOLS
