"""Introspect a Pegasus workflow_generator.py argparse parser.

Runs the generator up to ``parse_args()`` with a monkeypatched
``ArgumentParser`` that dumps the parameter schema as JSON and exits — so no
workflow files are written and there are no side effects beyond imports.

The studio uses this to build the Generate form dynamically: different
workflows expose different arguments, so we discover them at runtime rather
than hardcoding.

**Safety:** the "halt at parse_args" trick only stops a generator that actually
uses argparse. A tutorial-style generator with no argparse (e.g.
quickstart-workflow) runs its module top-level straight through — which may
``wf.plan(submit=True)`` and submit a real workflow. Since introspection is
triggered on every param fetch, that turns into a submit-loop. So we first
check (statically, without executing) whether the generator uses argparse at
all; if it doesn't, there is nothing to introspect and we return empty params
WITHOUT running the file.

Usage:
    python3 introspect_argparse.py <path-to-workflow_generator.py>

Output (stdout, last JSON line):
    {"params": [{dest, flag, flags, help, default, required, is_flag,
                 choices}], "mutex_required": [[dest, ...], ...]}
"""

from __future__ import annotations

import argparse
import ast
import json
import runpy
import sys


def _uses_argparse(target: str) -> bool:
    """True if the generator references argparse (import or ``argparse.`` use).

    Parsed statically so we never execute a generator that has no parser to
    introspect — executing such a script can have real side effects (writing
    files, planning and *submitting* a workflow at module top level)."""
    try:
        with open(target, encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read(), filename=target)
    except (OSError, SyntaxError):
        # Unreadable / unparseable — be conservative and don't execute it.
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "argparse" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "argparse":
                return True
        elif isinstance(node, ast.Attribute):
            # argparse.ArgumentParser(...) reached via ``import argparse``
            if isinstance(node.value, ast.Name) and node.value.id == "argparse":
                return True
    return False


def _schema_from_parser(parser: argparse.ArgumentParser) -> dict:
    mutex_required = []
    for grp in getattr(parser, "_mutually_exclusive_groups", []):
        if getattr(grp, "required", False):
            mutex_required.append([a.dest for a in grp._group_actions])

    params = []
    for act in parser._actions:
        if act.dest == "help" or not act.option_strings:
            continue
        is_flag = isinstance(
            act, (argparse._StoreTrueAction, argparse._StoreFalseAction)
        )
        default = act.default
        if default is argparse.SUPPRESS:
            default = None
        params.append({
            "dest": act.dest,
            # prefer the long (--foo) form for display/CLI building
            "flag": sorted(act.option_strings, key=len)[-1],
            "flags": list(act.option_strings),
            "help": act.help or "",
            "default": default,
            "required": bool(act.required),
            "is_flag": is_flag,
            "choices": [str(c) for c in act.choices] if act.choices else None,
        })
    return {"params": params, "mutex_required": mutex_required}


def _emit(payload: dict) -> None:
    sys.stdout.write("\n__ARGPARSE_SCHEMA__" + json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def main() -> int:
    if len(sys.argv) < 2:
        _emit({"params": [], "mutex_required": [], "error": "no target"})
        return 0
    target = sys.argv[1]

    # No argparse -> nothing to introspect, and running it could submit a
    # workflow. Return empty params without executing the file.
    if not _uses_argparse(target):
        _emit({"params": [], "mutex_required": []})
        return 0

    def patched(self, *args, **kwargs):  # noqa: ANN001
        _emit(_schema_from_parser(self))
        sys.exit(0)

    argparse.ArgumentParser.parse_args = patched
    argparse.ArgumentParser.parse_known_args = patched

    # Don't leak our own argv into the generator.
    sys.argv = [target]
    try:
        runpy.run_path(target, run_name="__main__")
    except SystemExit:
        raise
    except BaseException as e:  # generator import/build error
        _emit({"params": [], "mutex_required": [], "error": str(e)})
        return 1

    # parse_args was never called
    _emit({"params": [], "mutex_required": []})
    return 0


if __name__ == "__main__":
    sys.exit(main())
