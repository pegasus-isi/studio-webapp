"""Shared PATH / npm-prefix resolution for AI-CLI tools.

Studio's PVC mounts at ``~``, but the image ships a *root-owned* npm global
prefix at ``/opt/npm-global`` (only the image-baked ``opencode`` lives there).
A notebook runs as an unprivileged uid, so ``npm install -g`` into that prefix
fails with ``EACCES``. These helpers pick a HOME-writable prefix instead — so
user installs succeed without root and persist on the PVC — and expose the bin
dirs that must be on ``PATH`` for any process that launches a tool (the
installer, the in-app terminal PTYs, and tool starts).
"""

from __future__ import annotations

import os

# The image-baked, root-owned prefix. Kept on PATH so `opencode` still resolves.
IMAGE_NPM_PREFIX = "/opt/npm-global"


def _writable(path: str) -> bool:
    """True if *path* is writable, or its nearest existing ancestor is."""
    p = path
    while p and not os.path.exists(p):
        p = os.path.dirname(p)
    return bool(p) and os.access(p, os.W_OK)


def home_npm_prefix() -> str:
    """A HOME-based npm prefix (persists on the PVC, always user-writable)."""
    return os.path.join(os.environ.get("HOME", "/tmp"), ".npm-global")


def npm_prefix() -> str:
    """The npm global prefix to install into.

    Honor ``NPM_CONFIG_PREFIX`` when its module dir is actually writable by us;
    otherwise fall back to ``~/.npm-global`` so ``npm install -g`` succeeds
    without root.
    """
    env_prefix = os.environ.get("NPM_CONFIG_PREFIX")
    if env_prefix and _writable(os.path.join(env_prefix, "lib", "node_modules")):
        return env_prefix
    return home_npm_prefix()


def tool_bin_dirs() -> list[str]:
    """Bin dirs holding tool binaries, most-specific first (deduped later)."""
    dirs = [os.path.join(npm_prefix(), "bin")]
    home = os.environ.get("HOME", "")
    if home:
        dirs.append(os.path.join(home, ".local", "bin"))  # e.g. antigravity's `agy`
    dirs.append(os.path.join(IMAGE_NPM_PREFIX, "bin"))  # image-baked opencode
    return dirs


def with_tool_paths(env: dict[str, str]) -> dict[str, str]:
    """Return a copy of *env* with tool bin dirs prepended to PATH (idempotent)."""
    parts = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    for d in reversed(tool_bin_dirs()):
        if d and d not in parts:
            parts.insert(0, d)
    out = dict(env)
    out["PATH"] = os.pathsep.join(parts)
    return out
