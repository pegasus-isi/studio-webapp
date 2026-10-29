"""Dependency-free verification for chat file edits.

These checks read/parse files only: they never import the workflow's runtime
dependencies or execute the edited script. Workflow libraries live in the
Apptainer image, so importing them here would test the wrong environment.
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path
from typing import NamedTuple

import yaml


class VerificationIssue(NamedTuple):
    severity: str  # "error" | "warning"
    message: str


def verify_file(path: str) -> list[VerificationIssue] | None:
    """Return issues, or None when the file type has no safe check."""
    suffix = Path(path).suffix.lower()
    try:
        if suffix == ".py":
            return _verify_python(path)
        if suffix in {".yml", ".yaml"}:
            return _verify_yaml(path)
        if suffix == ".json":
            return _verify_json(path)
        if suffix == ".sh":
            return _verify_shell(path)
    except Exception as exc:  # noqa: BLE001 - verification must not break a turn
        return [VerificationIssue("warning", f"could not verify {path}: {exc}")]
    return None


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _verify_python(path: str) -> list[VerificationIssue]:
    source = _read(path)
    try:
        # Syntax only. Does not import modules or execute the file.
        compile(source, path, "exec")
    except (SyntaxError, ValueError) as exc:
        line = getattr(exc, "lineno", None)
        where = f" (line {line})" if line else ""
        return [VerificationIssue("error", f"Python syntax error{where}: {exc}")]
    return _verify_argparse(source)


def _verify_yaml(path: str) -> list[VerificationIssue]:
    try:
        yaml.safe_load(_read(path))
    except yaml.YAMLError as exc:
        return [VerificationIssue("error", f"YAML parse error: {exc}")]
    return []


def _verify_json(path: str) -> list[VerificationIssue]:
    try:
        json.loads(_read(path))
    except json.JSONDecodeError as exc:
        return [VerificationIssue("error", f"JSON parse error: {exc}")]
    return []


def _verify_shell(path: str) -> list[VerificationIssue]:
    proc = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return [VerificationIssue("error", f"shell syntax error: {detail}")]
    return []


def _argparse_dests(source: str) -> set[str] | None:
    """Dests declared by add_argument(); None means 'do not analyse'."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    dests: set[str] = set()
    found_parser = False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue
        found_parser = True

        # Dynamic **kwargs means we cannot know the full schema.
        if any(kw.arg is None for kw in node.keywords):
            return None

        dest_kw = next((kw.value for kw in node.keywords if kw.arg == "dest"), None)
        if isinstance(dest_kw, ast.Constant) and isinstance(dest_kw.value, str):
            dests.add(dest_kw.value)
            continue

        options = [
            arg.value
            for arg in node.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        ]
        if options:
            long_options = [opt for opt in options if opt.startswith("--")]
            chosen = long_options[0] if long_options else options[0]
            dests.add(chosen.lstrip("-").replace("-", "_"))
        elif node.args:
            positional = node.args[0]
            if isinstance(positional, ast.Constant) and isinstance(positional.value, str):
                dests.add(positional.value)

    return dests if found_parser else None


def _verify_argparse(source: str) -> list[VerificationIssue]:
    """Warn when args.<name> is not declared by this file's add_argument()."""
    dests = _argparse_dests(source)
    if dests is None:
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    parser_vars: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if (
            isinstance(call.func, ast.Attribute)
            and call.func.attr in {"parse_args", "parse_known_args"}
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            parser_vars.add(node.targets[0].id)

    if not parser_vars:
        return []

    missing: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in parser_vars
            and node.attr not in dests
        ):
            missing.add(node.attr)

    if not missing:
        return []
    names = ", ".join(sorted(missing))
    return [
        VerificationIssue(
            "warning",
            f"argparse defines no option for: {names}. "
            "Add add_argument(...) for them or use a different attribute.",
        )
    ]


def format_verification(path: str, issues: list[VerificationIssue]) -> str:
    if not issues:
        return "\nVerification: OK"
    parts: list[str] = []
    for issue in issues:
        if issue.severity == "error":
            parts.append(f"Verification failed for {path}: {issue.message}")
        else:
            parts.append(f"Verification warning for {path}: {issue.message}")
    return "\n" + "\n".join(parts)


def has_errors(issues: list[VerificationIssue] | None) -> bool:
    return bool(issues and any(i.severity == "error" for i in issues))


def warnings(issues: list[VerificationIssue] | None) -> list[str]:
    return [i.message for i in (issues or []) if i.severity == "warning"]
