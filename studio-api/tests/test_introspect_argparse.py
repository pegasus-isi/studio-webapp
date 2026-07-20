"""Introspection must never execute a side-effecting generator.

The studio introspects a workflow_generator.py to build the Generate form. That
runs the file, relying on a patched argparse.parse_args() to halt it. A tutorial
generator with no argparse (e.g. quickstart-workflow) has nothing to halt it and
would run straight through — including wf.plan(submit=True). Since introspection
fires on every param fetch, that became a submit-loop. These tests lock in that
a no-argparse generator is NOT executed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_INTROSPECT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "introspect_argparse.py"
)


def _run(target: str) -> dict:
    proc = subprocess.run(
        [sys.executable, _INTROSPECT, target],
        capture_output=True, text=True, timeout=30,
    )
    schema = {}
    for line in proc.stdout.splitlines():
        if line.startswith("__ARGPARSE_SCHEMA__"):
            schema = json.loads(line[len("__ARGPARSE_SCHEMA__"):])
    return schema


def test_no_argparse_generator_is_not_executed(tmp_path):
    """A generator without argparse must yield empty params AND never run — so
    a top-level side effect (here, writing a sentinel) does not happen."""
    sentinel = tmp_path / "SIDE_EFFECT_HAPPENED"
    gen = tmp_path / "workflow-generator.py"
    gen.write_text(
        "from pathlib import Path\n"
        f"Path(r'{sentinel}').write_text('submitted!')\n"
    )

    schema = _run(str(gen))
    assert schema == {"params": [], "mutex_required": []}
    assert not sentinel.exists(), "introspection executed a no-argparse generator"


def test_argparse_generator_is_introspected(tmp_path):
    """A generator with argparse is introspected, and execution halts at
    parse_args() so post-parse side effects don't run."""
    sentinel = tmp_path / "AFTER_PARSE"
    gen = tmp_path / "workflow_generator.py"
    gen.write_text(
        "import argparse\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--samples', required=True, help='n samples')\n"
        "p.add_argument('--fast', action='store_true', help='fast mode')\n"
        "args = p.parse_args()\n"
        f"Path(r'{sentinel}').write_text('ran')\n"
    )

    schema = _run(str(gen))
    dests = {p["dest"] for p in schema.get("params", [])}
    assert "samples" in dests and "fast" in dests
    fast = next(p for p in schema["params"] if p["dest"] == "fast")
    assert fast["is_flag"] is True
    assert not sentinel.exists(), "execution did not halt at parse_args()"


def test_unparseable_generator_is_not_executed(tmp_path):
    """A generator that can't be parsed is treated as having no parser (and is
    not executed) rather than crashing introspection."""
    gen = tmp_path / "workflow-generator.py"
    gen.write_text("def broken(:\n    pass\n")  # syntax error
    assert _run(str(gen)) == {"params": [], "mutex_required": []}
