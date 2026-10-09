"""Rename a workflow project's folder from the Analyses page.

A real move of ``<workflows>/<old>`` to ``<workflows>/<new>``, not a display
label: people asked for it because the folder name is what they see in
JupyterLab, the terminal and the chat's file paths.

Pegasus bakes absolute paths into everything it writes, which is what makes a
move risky and what the guards are for:

* **Nothing may be planning or running.** DAGMan on the access point holds the
  submit and scratch paths of a live run; moving the folder under it fails the
  run in ways that look like infrastructure faults.
* **Studio's own records follow the move** (run history, feedback episodes),
  so a run does not appear to vanish and reappear as a new one.
* **The project's catalogs are re-pointed.** ``sites.yml`` and
  ``pegasus.properties`` carry the scratch/output/catalog paths; left alone, a
  Plan without a fresh Generate would quietly recreate the old folder and
  write results there.

What it cannot fix: files inside past run directories (braindump, submit
files, rescue DAGs) keep the old path. Past runs stay listed and readable, but
resuming or re-running an old run in place will not work. The UI says so.
"""

from __future__ import annotations

import os
import re
from typing import Any

import structlog

log = structlog.get_logger()

#: A folder name a user can type and every tool can handle: no separators, no
#: leading dot (hidden, and "." / ".."), no spaces (Pegasus and HTCondor
#: arguments are space-split, which is how streamflow's gauge names broke).
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")

#: Run states that mean something still holds the project's paths.
_BUSY = frozenset({"running", "planning"})

#: Catalogs a generator writes at the project root with absolute paths in them.
_CATALOGS = (
    "sites.yml", "sites.yaml",
    "pegasus.properties",
    "replicas.yml", "replicas.yaml",
    "transformations.yml", "transformations.yaml",
    "workflow.yml", "workflow.yaml",
)
_MAX_CATALOG_BYTES = 5 * 1024 * 1024


class RenameError(Exception):
    """A rename refused for a reason the user can act on."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise RenameError(
            400,
            "Use letters, numbers, '-', '_' or '.', starting with a letter or "
            "number (no spaces or slashes).",
        )
    return name


def busy_reason(project: dict[str, Any]) -> str | None:
    """Why the project cannot move right now, or None."""
    for run in project.get("submitted_runs") or []:
        if run.get("status") in _BUSY:
            return (
                f"{run.get('run_name') or 'A run'} is {run.get('status')}. "
                "Wait for it to finish or cancel it, then rename."
            )
    return None


def _repoint_catalogs(new_dir: str, old_paths: list[str]) -> list[str]:
    """Rewrite `old_paths` → `new_dir` in the project's root catalogs.

    Only whole path components match: `/home/u/workflows/eq` must not rewrite
    `/home/u/workflows/eq2`. Returns the files changed.
    """
    pattern = re.compile(
        "|".join(re.escape(p) for p in sorted(set(old_paths), key=len, reverse=True))
        + r"(?=[/\s\"',:;]|$)",
        re.MULTILINE,
    )
    changed: list[str] = []
    for name in _CATALOGS:
        path = os.path.join(new_dir, name)
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        try:
            if os.path.getsize(path) > _MAX_CATALOG_BYTES:
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        updated = pattern.sub(new_dir, text)
        if updated != text:
            tmp = path + ".studio-rename"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(updated)
            os.replace(tmp, path)
            changed.append(name)
    return changed


def rename_project_dir(
    project: dict[str, Any], new_name: str, roots: list[str]
) -> dict[str, Any]:
    """Move the project folder. Raises RenameError; returns what changed."""
    new_name = validate_name(new_name)
    old_dir = project["project_dir"]
    old_real = os.path.realpath(old_dir)
    real_roots = [os.path.realpath(r) for r in roots]
    if not any(old_real.startswith(r + os.sep) for r in real_roots):
        raise RenameError(403, "Refusing to rename outside the workflows directory")
    if os.path.islink(old_dir):
        # Moving the link would leave the real folder (and its paths) behind.
        raise RenameError(409, "This project is a symbolic link; rename its target instead.")
    if new_name == os.path.basename(old_dir):
        raise RenameError(400, "That is already its name.")

    reason = busy_reason(project)
    if reason:
        raise RenameError(409, reason)

    new_dir = os.path.join(os.path.dirname(old_dir), new_name)
    if os.path.lexists(new_dir):
        raise RenameError(409, f"'{new_name}' already exists in your workspace.")

    try:
        os.rename(old_dir, new_dir)
    except OSError as e:
        # EBUSY on NFS when something holds the directory open; nothing moved.
        raise RenameError(409, f"Could not rename the folder: {e.strerror or e}") from e

    old_paths = [old_dir, old_real] if old_real != old_dir else [old_dir]
    catalogs = _repoint_catalogs(new_dir, old_paths)
    log.info(
        "project_renamed",
        old=os.path.basename(old_dir),
        new=new_name,
        catalogs=catalogs,
    )
    return {
        "old_dir": old_dir,
        "new_dir": new_dir,
        "old_paths": old_paths,
        "catalogs_updated": catalogs,
    }
