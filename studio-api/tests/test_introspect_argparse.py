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


# ── Phase 3 parameter hints (#241) ────────────────────────────────────────
#
# These are *hints*: the UI picks a nicer widget from them and falls back to a
# text input for anything it doesn't recognise. A wrong guess must never block
# submission, so the tests care most about the fallbacks being sane.


def _params(tmp_path, body: str) -> dict:
    gen = tmp_path / "workflow_generator.py"
    gen.write_text(
        "import argparse\n"
        "p = argparse.ArgumentParser()\n"
        f"{body}\n"
        "p.parse_args()\n"
    )
    return {p["dest"]: p for p in _run(str(gen))["params"]}


def test_types_are_inferred_from_argparse_and_names(tmp_path):
    params = _params(
        tmp_path,
        "p.add_argument('--num-subjects', type=int, default=24)\n"
        "p.add_argument('--threshold', type=float)\n"
        "p.add_argument('--input-file')\n"
        "p.add_argument('--output-dir')\n"
        "p.add_argument('--start-date')\n"
        "p.add_argument('--mode', choices=['a', 'b'])\n"
        "p.add_argument('--verbose', action='store_true')\n"
        "p.add_argument('--label')\n",
    )
    assert params["num_subjects"]["type"] == "integer"
    assert params["threshold"]["type"] == "number"
    assert params["input_file"]["type"] == "file"
    assert params["output_dir"]["type"] == "directory"
    assert params["start_date"]["type"] == "date"
    assert params["mode"]["type"] == "choice"
    assert params["verbose"]["type"] == "boolean"
    # Nothing to go on — plain text, which is what the form rendered before.
    assert params["label"]["type"] == "text"


def test_label_prefers_help_text_over_the_flag_name(tmp_path):
    params = _params(
        tmp_path,
        "p.add_argument('--num-subjects', type=int, default=24,\n"
        "               help='Number of subjects to process (default: 24)')\n",
    )
    # "num subjects" is what stripping dashes gives; the author wrote better.
    assert params["num_subjects"]["label"] == "Number of subjects to process"


def test_label_falls_back_to_the_flag_when_help_is_long_or_absent(tmp_path):
    params = _params(
        tmp_path,
        "p.add_argument('--subject-ids')\n"
        "p.add_argument('--min-subjects', help='"
        + "A very long explanation that would wrap and read as a paragraph "
        "rather than a field label, so it stays a description'"
        ")\n",
    )
    assert params["subject_ids"]["label"] == "Subject ids"
    assert params["min_subjects"]["label"] == "Min subjects"
    # The full help survives regardless — only the *label* falls back.
    assert "very long explanation" in params["min_subjects"]["help"]


def test_plumbing_is_marked_advanced_but_required_params_never_are(tmp_path):
    params = _params(
        tmp_path,
        "p.add_argument('--output', default='workflow.yml')\n"
        "p.add_argument('--skip-sites-catalog', action='store_true')\n"
        "p.add_argument('--execution-site', default='condorpool')\n"
        "p.add_argument('--num-subjects', type=int, default=24)\n"
        "p.add_argument('--required-output', dest='output_file', required=True)\n",
    )
    assert params["output"]["advanced"] is True
    assert params["skip_sites_catalog"]["advanced"] is True
    assert params["execution_site"]["advanced"] is True
    # The science parameter stays in the main form.
    assert params["num_subjects"]["advanced"] is False
    # Required beats the plumbing name every time: a form that hides something
    # you must fill in is worse than one that shows too much.
    assert params["output_file"]["advanced"] is False
