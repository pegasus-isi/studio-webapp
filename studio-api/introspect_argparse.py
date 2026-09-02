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
import re
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


# Dest names that identify a parameter as plumbing rather than science: the
# workflow's own bookkeeping, not something the person running it decided.
# Matched only against *optional* params — a required argument is never
# demoted, however it is named, because burying one makes the form unusable.
_PLUMBING_DESTS = frozenset({
    "output", "outfile", "out_file", "output_file", "workflow_file",
    "execution_site", "exec_site", "site", "output_site", "sites_catalog",
    "skip_sites_catalog", "skip_catalog", "tc", "rc", "sc",
    "verbose", "debug", "quiet", "dry_run", "force",
})

_PLUMBING_PREFIXES = ("skip_", "no_")


def _infer_type(act: argparse.Action, is_flag: bool) -> str:
    """Best-effort widget hint for a parameter.

    Deliberately a *hint*: every value is still a string on the command line,
    and the UI falls back to a plain text input for anything it doesn't
    recognise. A wrong guess here must never block submission — argparse
    remains the thing that validates, and it does so with the generator's own
    error messages.
    """
    if is_flag:
        return "boolean"
    if act.choices:
        return "choice"
    if act.type is int:
        return "integer"
    if act.type is float:
        return "number"

    # Name-based heuristics, checked against dest and metavar. These carry the
    # cases argparse cannot express: `type=str` tells us nothing about whether
    # a string is a path, a date, or free text.
    name = f"{act.dest} {act.metavar or ''}".lower()
    if any(k in name for k in ("_dir", "dir_", "directory", "folder")):
        return "directory"
    if any(k in name for k in ("_file", "file_", "_path", "path_", "input", "csv", "tsv")):
        return "file"
    if "date" in name:
        return "date"
    return "text"


def _label_for(act: argparse.Action) -> str:
    """A human label, preferring the author's help text over the flag name.

    Generator authors write real sentences in `help`; the flag is a CLI
    identifier. "Number of subjects to process" beats "num subjects", which is
    what stripping dashes off `--num-subjects` produces. Long help stays as the
    description and the flag-derived name is used instead, so a label never
    becomes a paragraph.
    """
    help_text = (act.help or "").strip()
    # argparse convention: authors append "(default: X)" — that belongs in the
    # placeholder, not the label.
    help_text = re.sub(r"\s*\(default:.*?\)\s*$", "", help_text).strip()
    # Take the first sentence only.
    first = re.split(r"(?<=[.!?])\s", help_text)[0].strip().rstrip(".")
    if first and len(first) <= 48:
        return first[0].upper() + first[1:]

    # Fall back to the flag. Longest option string first, because `--num-subjects`
    # says more than `-n`.
    for candidate in sorted(act.option_strings, key=len, reverse=True) + [act.dest]:
        pretty = candidate.lstrip("-").replace("-", " ").replace("_", " ").strip()
        # A generator that declares only a short flag (`-s`) alongside help text
        # too long to be a label produced the label "s" — and because
        # `_is_advanced` never demotes a required parameter, that single letter
        # was the name on the one field the user had to fill in (#314). Anything
        # this short is a CLI identifier, not a name.
        if len(pretty) > 2:
            return pretty[:1].upper() + pretty[1:]

    # Nothing usable anywhere: a truncated clause from the help beats a letter.
    if first:
        clipped = first[:45].rstrip()
        return clipped[0].upper() + clipped[1:] + "…"
    pretty = act.dest.replace("_", " ").strip() or act.dest
    return pretty[:1].upper() + pretty[1:]


def _is_advanced(dest: str, required: bool) -> bool:
    """True for plumbing that shouldn't compete with the science parameters.

    Never demotes a required parameter: a form that hides something you must
    fill in is worse than one that shows too much.
    """
    if required:
        return False
    return dest in _PLUMBING_DESTS or dest.startswith(_PLUMBING_PREFIXES)


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
            # Additive hints (#241, Phase 3). Older frontends ignore them and
            # keep rendering every parameter as a text input.
            "type": _infer_type(act, is_flag),
            "label": _label_for(act),
            "advanced": _is_advanced(act.dest, bool(act.required)),
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
