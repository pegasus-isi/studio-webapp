"""Where the user's own files live.

The workspace root is ``$HOME``. A workflow project is
``$HOME/workflows/<name>/``; uploaded data lands in ``$HOME/data/``.

It used to be ``$HOME/work``, a convention borrowed from JupyterHub images
where only that subdirectory is on the persistent volume. Studio has no such
split: the spawner mounts the whole home over NFS and sets ``HOME`` to it, so
the extra level marked nothing. What it did do was disagree with everything
that had already moved up to ``$HOME`` — JupyterLab's root, ``.studio``, the
generated ``CLAUDE.md``/``AGENTS.md``, the run scanner — leaving a workflow
written to ``$HOME/workflows/`` present on disk and absent from the UI, and a
page of prompt text teaching models a prefix rule to work around it.

Nothing is moved. ``$HOME/work/workflows`` is still read (see
``legacy_workflows_dirs``): those directories are on persistent NFS homes and
belong to users, not to us.

One definition, imported by every module that needs it — eleven copies of the
same ``os.environ.get(...)`` expression is how a default drifts.
"""

from __future__ import annotations

import os

# The pre-#401 workspace root, relative to $HOME. Read, never written to.
LEGACY_SUBDIR = "work"

# Where workflow projects live, relative to the workspace root.
WORKFLOWS_SUBDIR = "workflows"

# Where uploads land, relative to the workspace root. A single predictable
# directory is the point: a scientist should be able to answer "where did my
# file go?" without learning the layout (#241, Phase 2).
DATA_SUBDIR = "data"


def workspace_root() -> str:
    """The user's workspace root — ``$HOME`` unless overridden.

    ``STUDIO_WORKSPACE_DIR`` still wins, for deployments that hand Studio a
    directory that is not the home (Open OnDemand on a shared host).
    """
    explicit = os.environ.get("STUDIO_WORKSPACE_DIR")
    if explicit:
        return explicit
    return os.environ.get("HOME") or "/tmp"


def workflows_dir(root: str | None = None) -> str:
    """Where new workflow projects go."""
    return os.path.join(root or workspace_root(), WORKFLOWS_SUBDIR)


def data_dir(root: str | None = None) -> str:
    """Where uploaded data lands."""
    return os.path.join(root or workspace_root(), DATA_SUBDIR)


def legacy_workspace_root(root: str | None = None) -> str:
    """The pre-#401 workspace, ``$HOME/work``.

    Only for cleaning up after it — generated tool config that was written
    there when it was the workspace, and which still shadows the live copy at
    $HOME whenever a shell starts in it. Nothing is written here.
    """
    return os.path.join(root or workspace_root(), LEGACY_SUBDIR)


def legacy_workflows_dirs(root: str | None = None) -> list[str]:
    """Older project directories that are still listed, never written to.

    Only ``$HOME/work/workflows``. Returned empty when it does not exist or
    when it would duplicate the current one, so a caller can concatenate
    without checking.
    """
    root = root or workspace_root()
    legacy = os.path.join(root, LEGACY_SUBDIR, WORKFLOWS_SUBDIR)
    if legacy == workflows_dir(root) or not os.path.isdir(legacy):
        return []
    return [legacy]


# Directories under the workspace root that the file API and the chat file
# tools must not reach, read or write.
#
# They only became reachable when the root moved from $HOME/work up to $HOME
# (#401). Each one holds a credential or Studio's own state:
#
#   .studio        the SQLite DB — settings, chat history, LLM keys
#   .ssh           the user's keys
#   .condor        the HTCondor IDTOKEN the spawner mints (tokens.d/), which is
#                  submit rights on the pool
#   .config, .local  tool credentials and the marketplace checkout
#
# Reading is denied, not just writing: an assistant that can read a token can
# put it in a message, and the point of the boundary is that it cannot.
#
# This is not a sandbox for `run_command` — that gets a shell and can `cd`
# anywhere, exactly as it could before. It is the file tools' boundary, which
# is the one users see in the browser.
PROTECTED_SUBDIRS = (
    ".studio",
    ".ssh",
    ".condor",
    ".config",
    ".local",
    ".gnupg",
)


def is_protected(resolved: str, root: str | None = None) -> bool:
    """True if a resolved absolute path is inside a protected directory.

    ``resolved`` must already be realpath'd by the caller: a symlink into
    ``.ssh`` is the case this has to catch.
    """
    root = os.path.realpath(root or workspace_root())
    for name in PROTECTED_SUBDIRS:
        protected = os.path.join(root, name)
        if resolved == protected or resolved.startswith(protected + os.sep):
            return True
    return False
