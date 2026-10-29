"""Block edits for the builtin chat file tools.

`edit_blocks` applies several block replacements across files in one call.
Matching and applying live here so the unit tests can exercise them without a
streaming loop. Path policy stays in ``routers.chat._safe_path``; callers pass
``resolve_path`` / ``display_path`` callables so this module has no router
import (and no circular import).
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from services.chat_verify import verify_file

MAX_EDITS = 20
MAX_PATCH_CHARS = 200_000
MAX_FILE_BYTES = 10 * 1024 * 1024


class EditFailure(Exception):
    """One edit could not be applied; the message is safe to show the model."""


@dataclass
class EditOutcome:
    """Result of a per-file atomic edit batch."""

    text: str
    applied: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    verification_failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# One-to-one typographic normalization. Every replacement maps a single
# character to a single character, so offsets found in the normalized text
# remain valid in the original text.
_TRANSLATE = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u00a0": " ", "\u2002": " ", "\u2003": " ",
    "\u2004": " ", "\u2005": " ", "\u2006": " ",
    "\u2007": " ", "\u2008": " ", "\u2009": " ",
    "\u200a": " ", "\u202f": " ", "\u205f": " ", "\u3000": " ",
})


def _normalize(text: str) -> str:
    return text.translate(_TRANSLATE)


def _find_matches(text: str, old: str) -> list[tuple[int, int, str]]:
    """Return (start, end, kind) for every occurrence of ``old`` in ``text``.

    Tries exact, then normalized, then trailing-whitespace-insensitive line
    matching. The first tier that finds anything wins.
    """
    # 1. exact
    exact: list[tuple[int, int, str]] = []
    start = 0
    while (i := text.find(old, start)) != -1:
        exact.append((i, i + len(old), "exact"))
        start = i + len(old)
    if exact:
        return exact

    # 2. normalized (offsets are preserved because _normalize is 1:1)
    norm_text, norm_old = _normalize(text), _normalize(old)
    normalized: list[tuple[int, int, str]] = []
    start = 0
    while (i := norm_text.find(norm_old, start)) != -1:
        normalized.append((i, i + len(norm_old), "normalized"))
        start = i + len(norm_old)
    if normalized:
        return normalized

    # 3. line-wise, ignoring trailing whitespace on each line
    old_lines = old.replace("\r\n", "\n").split("\n")
    if old_lines and old_lines[-1] == "":
        old_lines.pop()

    lines = text.splitlines(keepends=True)
    starts: list[int] = []
    pos = 0
    for line in lines:
        starts.append(pos)
        pos += len(line)

    matches: list[tuple[int, int, str]] = []
    n = len(old_lines)
    for i in range(len(lines) - n + 1):
        if all(
            lines[i + j].rstrip("\r\n").rstrip() == old_lines[j].rstrip()
            for j in range(n)
        ):
            start = starts[i]
            end = starts[i + n] if i + n < len(starts) else len(text)
            matches.append((start, end, "line"))
    return matches


def _adapt_newlines(new: str, text: str) -> str:
    """Keep the file's CRLF style unless the replacement already uses CRLF."""
    if "\r\n" in text and "\r\n" not in new:
        return new.replace("\n", "\r\n")
    return new


def _apply_one(
    text: str,
    old: str,
    new: str,
    replace_all: bool,
) -> tuple[str, int]:
    """Replace ``old`` in ``text``; returns (updated_text, replacements)."""
    if old == "":
        raise EditFailure("old_string must not be empty")
    if old == new:
        raise EditFailure("old_string and new_string are identical")

    matches = _find_matches(text, old)
    if not matches:
        raise EditFailure(
            "old_string was not found. Read the exact range again and retry."
        )
    if len(matches) > 1 and not replace_all:
        lines = ", ".join(
            str(text.count("\n", 0, start) + 1) for start, _, _ in matches[:8]
        )
        raise EditFailure(
            f"old_string occurs {len(matches)} times (lines {lines}); "
            "add more context or set replace_all: true."
        )

    selected = matches if replace_all else matches[:1]
    replacement = _adapt_newlines(new, text)
    for start, end, _ in sorted(selected, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text, len(selected)


def _diff_counts(before: str, after: str) -> tuple[int, int]:
    """Return (added, removed) line counts for a diff summary."""
    added = removed = 0
    for line in difflib.unified_diff(
        before.splitlines(), after.splitlines(), n=0
    ):
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _looks_like_diff(new: str, old: str) -> bool:
    """Detect a model pasting unified-diff lines into new_string.

    Leading '-' is deliberately not treated as a marker: YAML lists legitimately
    start every line with '- '. A leading '+' (including a single line) is the
    mistake this model makes, and final text rarely starts a line with '+'.
    """
    lines = [line for line in new.splitlines() if line.strip()]
    if not lines:
        return False
    if any(line.startswith(("@@", "--- ", "+++ ")) for line in lines):
        return True
    if lines[0].startswith("+"):
        first_old = next((line for line in old.splitlines() if line.strip()), "")
        if not first_old.startswith("+"):
            return True
    plus = sum(1 for line in lines if line.startswith("+"))
    return len(lines) >= 2 and plus >= max(2, len(lines) // 2)


def apply_edits(
    edits: Any,
    *,
    resolve_path: Callable[[str], str],
    display_path: Callable[[str], str],
) -> EditOutcome:
    """Apply edits per file atomically.

    A failure in one file does not discard successful files. No-op edits are
    skipped with a warning. The result names every applied, skipped, and failed
    file. Verification is syntax/parse-only and never imports workflow code.
    """
    if not isinstance(edits, list) or not edits:
        return EditOutcome(text="Error: edits must be a non-empty array")
    if len(edits) > MAX_EDITS:
        return EditOutcome(text=f"Error: at most {MAX_EDITS} edits per call")

    current: dict[str, str] = {}
    original: dict[str, str] = {}
    has_bom: dict[str, bool] = {}
    order: list[str] = []
    blocks: dict[str, int] = {}
    failed: dict[str, list[str]] = {}
    failed_resolved: set[str] = set()
    skipped: dict[str, list[str]] = {}
    warnings: list[str] = []
    total_chars = 0

    def fail(label: str, message: str) -> None:
        failed.setdefault(label, []).append(message)

    for index, edit in enumerate(edits, 1):
        if not isinstance(edit, dict):
            fail(f"edit {index}", "must be an object")
            continue

        raw_path = edit.get("path")
        old = edit.get("old_string")
        new = edit.get("new_string")
        replace_all = bool(edit.get("replace_all", False))

        if not isinstance(raw_path, str) or not raw_path:
            fail(f"edit {index}", "path is required")
            continue
        label = raw_path
        if not isinstance(old, str) or not isinstance(new, str):
            fail(label, f"edit {index}: old_string and new_string must be strings")
            continue

        total_chars += len(old) + len(new)
        if total_chars > MAX_PATCH_CHARS:
            fail(label, f"edit {index}: patch exceeds {MAX_PATCH_CHARS} characters")
            continue

        try:
            path = resolve_path(raw_path)
        except (ValueError, OSError) as exc:
            fail(label, f"edit {index}: {exc}")
            continue

        label = display_path(path)

        if not os.path.isfile(path):
            fail(label, f"edit {index}: not an existing file. Use write_file for a new file.")
            failed_resolved.add(path)
            continue

        if path not in current:
            try:
                with open(path, "rb") as f:
                    raw = f.read()
            except OSError as exc:
                fail(label, f"edit {index}: {exc}")
                failed_resolved.add(path)
                continue

            if len(raw) > MAX_FILE_BYTES:
                fail(label, f"edit {index}: file is larger than {MAX_FILE_BYTES} bytes")
                failed_resolved.add(path)
                continue
            if b"\x00" in raw[:4096]:
                fail(label, f"edit {index}: not a text file")
                failed_resolved.add(path)
                continue

            try:
                has_bom[path] = raw.startswith(b"\xef\xbb\xbf")
                text = raw.decode("utf-8-sig" if has_bom[path] else "utf-8")
            except UnicodeError as exc:
                fail(label, f"edit {index}: {exc}")
                failed_resolved.add(path)
                continue

            original[path] = text
            current[path] = text
            order.append(path)

        if old == new:
            skipped.setdefault(label, []).append(
                f"edit {index}: old_string and new_string are identical"
            )
            continue

        if _looks_like_diff(new, old):
            fail(
                label,
                f"edit {index}: new_string looks like a unified diff; "
                "pass the final text without leading + or - markers",
            )
            failed_resolved.add(path)
            continue

        try:
            current[path], _ = _apply_one(current[path], old, new, replace_all)
        except EditFailure as exc:
            fail(label, f"edit {index}: {exc}")
            failed_resolved.add(path)
            continue

        blocks[path] = blocks.get(path, 0) + 1

    # Write only files with no failures.
    written: list[tuple[str, str, str]] = []
    for path in order:
        if path in failed_resolved:
            continue
        if current[path] == original[path]:
            continue
        try:
            with open(
                path,
                "w",
                encoding="utf-8-sig" if has_bom[path] else "utf-8",
                newline="",
            ) as f:
                f.write(current[path])
        except OSError as exc:
            fail(display_path(path), f"write failed: {exc}")
            continue
        written.append((path, original[path], current[path]))

    # Syntax/parse verification plus the AST argparse warning.
    verification_failed: list[str] = []
    verification_lines: list[str] = []
    verified_any = False
    for path, _, _ in written:
        issues = verify_file(path)
        if issues is None:
            continue
        verified_any = True
        for issue in issues:
            if issue.severity == "error":
                verification_failed.append(display_path(path))
                verification_lines.append(
                    f"Verification failed for {display_path(path)}: {issue.message}"
                )
            else:
                warnings.append(
                    f"Verification warning for {display_path(path)}: {issue.message}"
                )

    lines: list[str] = []
    if written:
        applied_blocks = sum(blocks.get(path, 0) for path, _, _ in written)
        lines.append(f"Edited {len(written)} file(s) ({applied_blocks} block(s)):")
        for path, before, after in written:
            added, removed = _diff_counts(before, after)
            lines.append(f"M {display_path(path)} (-{removed} +{added})")

    if skipped:
        lines.append(
            f"Skipped {sum(len(v) for v in skipped.values())} edit(s) (no change):"
        )
        for label, entries in skipped.items():
            for entry in entries:
                lines.append(f"S {label} — {entry}")

    if failed:
        lines.append(
            f"Failed {len(failed)} file(s) "
            f"({sum(len(v) for v in failed.values())} edit(s)):"
        )
        for label, entries in failed.items():
            lines.append(f"! {label} — " + "; ".join(entries))
        lines.append("Nothing was written for the failed file(s).")

    lines.extend(verification_lines)
    if verified_any and not verification_failed:
        lines.append("Verification: OK")
    for warning in warnings:
        lines.append(warning)
    if not lines:
        lines.append("Warning: no files were changed (every edit was a no-op).")

    text = "\n".join(lines)
    if failed and not written:
        text = "Error: no files were changed.\n" + text

    return EditOutcome(
        text=text,
        applied=[display_path(path) for path, _, _ in written],
        failed=list(failed),
        skipped=[entry for entries in skipped.values() for entry in entries],
        verification_failed=verification_failed,
        warnings=warnings,
    )
