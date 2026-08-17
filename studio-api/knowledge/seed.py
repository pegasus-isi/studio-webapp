"""Materialize a Claude Code plugin seed directory.

Claude Code can be handed a read-only directory of marketplaces at startup via
``CLAUDE_CODE_PLUGIN_SEED_DIR``. That is how Studio (and the OOD app, which runs
in a home directory it does not own) point the tool at the marketplace checkout
without cloning anything at runtime and without writing into the user's
``~/.claude``.

Three requirements are encoded here because every one of them fails *silently*:

1. ``installLocation`` and ``lastUpdated`` are mandatory. An entry carrying only
   ``source`` fails schema validation and the marketplace is skipped, with a
   warning that is invisible at normal verbosity.
2. The key a marketplace is registered under must equal the ``name`` in its own
   manifest. Claude Code enforces this and reports a mismatch as "plugin not
   found in marketplace <key>", which sends you looking in the wrong place.
3. Seeded marketplaces are read-only to the user and are skipped during refresh.
   That is the point — content follows the image — but it means updates happen by
   updating the checkout (``git pull``), not through ``claude plugin``.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

import structlog

from knowledge import layout

log = structlog.get_logger()


def marketplace_manifest(marketplace_dir: str | None = None) -> dict:
    """Read the marketplace manifest from a checkout."""
    root = marketplace_dir or layout.MARKETPLACE_ROOT
    with open(os.path.join(root, ".claude-plugin", "marketplace.json")) as fh:
        return json.load(fh)


def marketplace_name(marketplace_dir: str | None = None) -> str:
    """The marketplace's declared name — the only key it may be registered as."""
    return marketplace_manifest(marketplace_dir)["name"]


def write_seed(
    dest: str,
    *,
    marketplace_dir: str | None = None,
    copy: bool = False,
) -> str:
    """Write a seed directory at ``dest`` pointing at the marketplace checkout.

    Produces the layout Claude Code syncs at startup::

        <dest>/known_marketplaces.json
        <dest>/marketplaces/<name>   -> the checkout

    Returns ``dest``, for ``CLAUDE_CODE_PLUGIN_SEED_DIR``.

    ``marketplaces/<name>`` is a symlink by default, so there is still one copy
    on disk and a ``git pull`` in the checkout is visible immediately. Pass
    ``copy=True`` where symlinks are unavailable, or where the seed must not
    change under a running tool.
    """
    root = os.path.abspath(marketplace_dir or layout.MARKETPLACE_ROOT)
    name = marketplace_name(root)

    markets = os.path.join(dest, "marketplaces")
    os.makedirs(markets, exist_ok=True)

    target = os.path.join(markets, name)
    if os.path.islink(target) or os.path.exists(target):
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target)
        else:
            os.unlink(target)

    if copy:
        shutil.copytree(root, target)
    else:
        try:
            os.symlink(root, target, target_is_directory=True)
        except OSError as exc:
            log.warning("seed_symlink_failed_copying", error=str(exc))
            shutil.copytree(root, target)

    entry = {
        name: {
            "source": {"source": "directory", "path": root},
            "installLocation": target,
            "lastUpdated": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }
    }
    with open(os.path.join(dest, "known_marketplaces.json"), "w") as fh:
        json.dump(entry, fh, indent=2)
        fh.write("\n")

    log.info("plugin_seed_written", dest=dest, marketplace=name, source=root)
    return dest
