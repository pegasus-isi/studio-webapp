"""Introspect a Pegasus workflow_generator.py argparse parser.

Runs the generator up to ``parse_args()`` with a monkeypatched
``ArgumentParser`` that dumps the parameter schema as JSON and exits — so no
workflow files are written and there are no side effects beyond imports.

The studio uses this to build the Generate form dynamically: different
workflows expose different arguments, so we discover them at runtime rather
than hardcoding.

Usage:
    python3 introspect_argparse.py <path-to-workflow_generator.py>

Output (stdout, last JSON line):
    {"params": [{dest, flag, flags, help, default, required, is_flag,
                 choices}], "mutex_required": [[dest, ...], ...]}
"""

from __future__ import annotations

import argparse
import json
import runpy
import sys


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
