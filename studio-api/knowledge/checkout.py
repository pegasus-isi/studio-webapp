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
import shutil
import subprocess
from typing import Any

import structlog

from knowledge import layout

log = structlog.get_logger()

# Short: this runs on the startup path, and a hung fetch must never be what
# keeps a user waiting for their server.
GIT_TIMEOUT_S = int(os.environ.get("STUDIO_KNOWLEDGE_GIT_TIMEOUT", "20"))

# Kept in step with knowledge/claude_code.py DEFAULT_PLUGINS.
DEFAULT_REFRESH_PLUGINS = ["pegasus-ai@scitech", "pegasus-dev@scitech"]


# Repo-local and global git config are user-writable and live on the
# persistent home. These override any value from .git/config, ~/.gitconfig or
# the environment, so a hook, a credential helper or an fsmonitor command
# planted there is never what this process runs. Env form (GIT_CONFIG_COUNT,
# git >= 2.31) so the same list can be mirrored by the entrypoint.
_GIT_CONFIG: tuple[tuple[str, str], ...] = (
    ("core.hooksPath", "/dev/null"),
    ("core.fsmonitor", "false"),
    ("credential.helper", ""),  # empty value resets the helper list
)


def _git_env() -> dict[str, str]:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        # Overrides core.askPass; /bin/true answers with nothing, so an auth
        # challenge from a repointed origin fails instead of hanging or
        # running whatever askpass the repo config names.
        "GIT_ASKPASS": "/bin/true",
        "GIT_SSH_COMMAND": "/bin/false",
        # ext:: runs commands; ssh would use the user's keys. `file` stays
        # allowed because the entrypoint's fallback leaves origin pointing at
        # the image seed under /opt when the remote set-url fails.
        "GIT_ALLOW_PROTOCOL": "http:https:file",
        "GIT_CONFIG_COUNT": str(len(_GIT_CONFIG)),
    }
    for i, (key, value) in enumerate(_GIT_CONFIG):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


def _trusted_upstream(url: str) -> bool:
    # Absolute local paths cover the image seed (/opt/pegasus-ai/marketplace)
    # that origin falls back to when the entrypoint's set-url fails.
    return url.startswith(("https://", "http://")) or os.path.isabs(url)


def _git(root: str, *args: str, timeout: int | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            timeout=timeout or GIT_TIMEOUT_S,
            env=_git_env(),
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
        "upstream": None,
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

    # Where a pull would fetch from. Reported so a repointed origin is visible
    # in the UI and the startup log, not only as a refused update.
    rc, out = _git(root, "remote", "get-url", "origin")
    if rc == 0 and out:
        info["upstream"] = out

    return info


def refresh_installed_plugins(plugins: list[str] | None = None) -> None:
    """Ask Claude Code to re-materialize its plugin cache from the checkout.

    Claude Code copies a plugin out of its marketplace at install time into
    ~/.claude/plugins/cache. When the marketplace is a directory that we then
    fast-forward, that cache is the one copy a `git pull` does not reach — a pod
    was found serving skills from an install weeks earlier, naming a skill that
    had since been withdrawn.

    Whether it re-copies on its own for a directory source is not something this
    code should depend on, so ask explicitly. `plugin update` is local,
    idempotent and a no-op when already current; failure is not interesting
    enough to surface, since the checkout itself is already correct.
    """
    if not shutil.which("claude"):
        return
    for plugin in plugins or DEFAULT_REFRESH_PLUGINS:
        try:
            subprocess.run(
                ["claude", "plugin", "update", plugin],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_S,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.info("plugin_refresh_skipped", plugin=plugin, error=str(exc))


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

    upstream = before.get("upstream") or ""
    if not _trusted_upstream(upstream):
        # A repointed remote is the persistence vector: ssh:// would use the
        # user's keys, ext:: runs a command. Refusing here gives the UI a
        # reason; GIT_ALLOW_PROTOCOL is the backstop if this is bypassed.
        log.warning("knowledge_update_refused_upstream", path=root, upstream=upstream)
        return {**before, "updated": False, "reason": "origin is not an http(s) remote"}

    rc, out = _git(root, "pull", "--ff-only", "--quiet")
    if rc != 0:
        # Offline, no upstream, or a diverged branch. Expected often enough that
        # it is info, not a warning.
        log.info("knowledge_update_skipped", path=root, error=out[:200])
        return {**before, "updated": False, "reason": out[:200] or "pull failed"}

    after = status()
    changed = after["sha"] != before["sha"]
    if changed:
        refresh_installed_plugins()
        log.info(
            "knowledge_updated",
            path=root,
            was=before["short_sha"],
            now=after["short_sha"],
        )
    return {
        **after,
        "updated": changed,
        "reason": None,
        "previous_sha": before["short_sha"],
    }
