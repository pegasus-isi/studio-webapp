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
from typing import Any, Callable

MAX_EDITS = 20
MAX_PATCH_CHARS = 200_000
MAX_FILE_BYTES = 10 * 1024 * 1024


class EditFailure(Exception):
    """One edit could not be applied; the message is safe to show the model."""


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


def apply_edits(
    edits: Any,
    *,
    resolve_path: Callable[[str], str],
    display_path: Callable[[str], str],
) -> str:
    """Apply a list of block replacements atomically.

    Returns a short result string. On any validation or matching failure no
    file is written and the string starts with "Error:".
    """
    if not isinstance(edits, list) or not edits:
        return "Error: edits must be a non-empty array"
    if len(edits) > MAX_EDITS:
        return f"Error: at most {MAX_EDITS} edits per call"

    current: dict[str, str] = {}
    original: dict[str, str] = {}
    has_bom: dict[str, bool] = {}
    blocks: dict[str, int] = {}
    total_chars = 0

    try:
        for index, edit in enumerate(edits, 1):
            if not isinstance(edit, dict):
                raise EditFailure(f"edit {index} must be an object")

            raw_path = edit.get("path")
            old = edit.get("old_string")
            new = edit.get("new_string")
            replace_all = bool(edit.get("replace_all", False))

            if not isinstance(raw_path, str) or not raw_path:
                raise EditFailure(f"edit {index}: path is required")
            if not isinstance(old, str) or not isinstance(new, str):
                raise EditFailure(
                    f"edit {index}: old_string and new_string must be strings"
                )

            total_chars += len(old) + len(new)
            if total_chars > MAX_PATCH_CHARS:
                raise EditFailure(
                    f"patch exceeds {MAX_PATCH_CHARS} characters"
                )

            # Raises ValueError for protected/outside/knowledge paths.
            path = resolve_path(raw_path)

            if not os.path.isfile(path):
                raise EditFailure(
                    f"edit {index}: not an existing file: "
                    f"{display_path(path)}. Use write_file for a new file."
                )

            if path not in current:
                with open(path, "rb") as f:
                    raw = f.read()
                if len(raw) > MAX_FILE_BYTES:
                    raise EditFailure(
                        f"edit {index}: {display_path(path)} is larger than "
                        f"{MAX_FILE_BYTES} bytes"
                    )
                if b"\x00" in raw[:4096]:
                    raise EditFailure(
                        f"edit {index}: not a text file: {display_path(path)}"
                    )
                has_bom[path] = raw.startswith(b"\xef\xbb\xbf")
                text = raw.decode("utf-8-sig" if has_bom[path] else "utf-8")
                original[path] = text
                current[path] = text

            try:
                current[path], _ = _apply_one(
                    current[path], old, new, replace_all
                )
            except EditFailure as exc:
                raise EditFailure(
                    f"edit {index} ({display_path(path)}): {exc}"
                ) from exc

            blocks[path] = blocks.get(path, 0) + 1

    except (EditFailure, ValueError, OSError, UnicodeError) as exc:
        return f"Error: {exc}"

    # All edits validated. Write only now.
    written: list[tuple[str, str, str]] = []
    for path, text in current.items():
        if text == original[path]:
            continue
        try:
            with open(
                path,
                "w",
                encoding="utf-8-sig" if has_bom[path] else "utf-8",
                newline="",
            ) as f:
                f.write(text)
        except OSError as exc:
            done = ", ".join(display_path(p) for p, _, _ in written) or "none"
            return (
                f"Error: could not write {display_path(path)}: {exc}. "
                f"Completed before failure: {done}"
            )
        written.append((path, original[path], text))

    if not written:
        return "Error: the edits leave every file unchanged"

    lines = [
        f"Edited {len(written)} file(s) ({sum(blocks.values())} block(s)):"
    ]
    for path, before, after in written:
        added, removed = _diff_counts(before, after)
        lines.append(f"M {display_path(path)} (-{removed} +{added})")
    return "\n".join(lines)
