"""Inspect and update the marketplace checkout.

The checkout is a plain git working copy in the user's own ``$HOME``, so its
version is a commit SHA rather than a package version that somebody has to
remember to bump. That makes two things possible which a baked, root-owned tree
could not offer: reporting exactly which skills a session is running, and
updating them without a new image.

Updating is deliberately conservative. A user may have edited a skill or pinned
an older commit on purpose, and silently discarding either would be worse than
being out of date, so a pull happens only when the checkout is clean and has no
local commits.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

import structlog

from knowledge import layout

log = structlog.get_logger()

# Short: this runs on the startup path, and a hung fetch must never be what
# keeps a user waiting for their server.
GIT_TIMEOUT_S = int(os.environ.get("STUDIO_KNOWLEDGE_GIT_TIMEOUT", "20"))


def _git(root: str, *args: str, timeout: int | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            timeout=timeout or GIT_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or proc.stderr).strip()


def status() -> dict[str, Any]:
    """Describe the checkout tools are reading.

    ``dirty`` and ``ahead`` are what gate an automatic update; they are reported
    so the reason an update is declined is visible rather than mysterious.
    """
    root = layout.marketplace_root()
    info: dict[str, Any] = {
        "path": root,
        "exists": os.path.isdir(os.path.join(root, ".claude-plugin")),
        "is_git": os.path.isdir(os.path.join(root, ".git")),
        "sha": None,
        "short_sha": None,
        "subject": None,
        "committed_at": None,
        "dirty": False,
        "ahead": 0,
        "skills": len(layout.skill_names()),
    }
    if not info["is_git"]:
        return info

    rc, out = _git(root, "log", "-1", "--format=%H%x1f%h%x1f%s%x1f%cI")
    if rc == 0 and "\x1f" in out:
        sha, short, subject, when = out.split("\x1f", 3)
        info.update(
            sha=sha, short_sha=short, subject=subject, committed_at=when,
        )

    rc, out = _git(root, "status", "--porcelain")
    info["dirty"] = rc == 0 and bool(out)

    rc, out = _git(root, "rev-list", "--count", "@{u}..HEAD")
    if rc == 0 and out.isdigit():
        info["ahead"] = int(out)

    return info


def update(*, force: bool = False) -> dict[str, Any]:
    """Fast-forward the checkout to its upstream.

    Returns a result dict rather than raising: this is called from the startup
    path, where being offline or behind a proxy is normal and must never be
    fatal. ``force`` skips only the local-commit guard, never the dirty guard —
    uncommitted edits are the one thing there is no way to recover.
    """
    before = status()
    root = before["path"]

    if not before["is_git"]:
        return {**before, "updated": False, "reason": "not a git checkout"}
    if before["dirty"]:
        return {**before, "updated": False, "reason": "local modifications"}
    if before["ahead"] and not force:
        return {**before, "updated": False, "reason": "local commits"}

    rc, out = _git(root, "pull", "--ff-only", "--quiet")
    if rc != 0:
        # Offline, no upstream, or a diverged branch. Expected often enough that
        # it is info, not a warning.
        log.info("knowledge_update_skipped", path=root, error=out[:200])
        return {**before, "updated": False, "reason": out[:200] or "pull failed"}

    after = status()
    changed = after["sha"] != before["sha"]
    if changed:
        log.info(
            "knowledge_updated",
            path=root,
            was=before["short_sha"],
            now=after["short_sha"],
        )
    return {**after, "updated": changed, "reason": None}
