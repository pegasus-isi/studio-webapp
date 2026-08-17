"""Tests for the Claude Code plugin seed writer.

Each assertion here corresponds to a failure that is silent in practice: a
missing field drops the marketplace with an invisible warning, and a key that
does not match the manifest name surfaces as "plugin not found".
"""

from __future__ import annotations

import json
import os

import pytest

from knowledge import seed


@pytest.fixture
def marketplace(tmp_path):
    """A minimal marketplace checkout."""
    root = tmp_path / "marketplace"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "scitech",
        "owner": {"name": "Pegasus"},
        "plugins": [{"name": "pegasus-ai", "source": "./plugins/pegasus-ai"}],
    }))
    (root / "plugins" / "pegasus-ai" / "skills").mkdir(parents=True)
    return root


def test_seed_layout(marketplace, tmp_path):
    dest = seed.write_seed(str(tmp_path / "seed"), marketplace_dir=str(marketplace))

    known = json.loads(
        (tmp_path / "seed" / "known_marketplaces.json").read_text()
    )
    assert list(known) == ["scitech"]
    assert os.path.isdir(os.path.join(dest, "marketplaces", "scitech"))


def test_entry_carries_the_mandatory_fields(marketplace, tmp_path):
    """An entry with only `source` is dropped, and the warning is invisible."""
    seed.write_seed(str(tmp_path / "seed"), marketplace_dir=str(marketplace))
    entry = json.loads(
        (tmp_path / "seed" / "known_marketplaces.json").read_text()
    )["scitech"]

    assert entry["installLocation"]
    assert entry["lastUpdated"].endswith("Z")
    assert entry["source"] == {
        "source": "directory", "path": str(marketplace.resolve()),
    }


def test_key_matches_the_manifest_name(marketplace, tmp_path):
    """Registering under any other key fails as 'plugin not found'."""
    (marketplace / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "something-else", "plugins": [],
    }))
    seed.write_seed(str(tmp_path / "seed"), marketplace_dir=str(marketplace))

    known = json.loads(
        (tmp_path / "seed" / "known_marketplaces.json").read_text()
    )
    assert list(known) == ["something-else"]
    assert os.path.isdir(
        os.path.join(tmp_path, "seed", "marketplaces", "something-else")
    )


def test_symlink_keeps_one_copy_so_git_pull_is_visible(marketplace, tmp_path):
    dest = seed.write_seed(str(tmp_path / "seed"), marketplace_dir=str(marketplace))
    target = os.path.join(dest, "marketplaces", "scitech")
    assert os.path.islink(target)

    # Updating the checkout is immediately visible through the seed — this is
    # what makes `git pull` the update mechanism.
    (marketplace / "plugins" / "pegasus-ai" / "skills" / "new").mkdir()
    assert os.path.isdir(os.path.join(target, "plugins", "pegasus-ai", "skills", "new"))


def test_copy_mode_detaches_from_the_checkout(marketplace, tmp_path):
    dest = seed.write_seed(
        str(tmp_path / "seed"), marketplace_dir=str(marketplace), copy=True
    )
    target = os.path.join(dest, "marketplaces", "scitech")
    assert not os.path.islink(target)

    (marketplace / "plugins" / "pegasus-ai" / "skills" / "new").mkdir()
    assert not os.path.isdir(
        os.path.join(target, "plugins", "pegasus-ai", "skills", "new")
    )


def test_rewrite_replaces_a_previous_seed(marketplace, tmp_path):
    """Re-seeding is idempotent, including switching between link and copy."""
    dest = str(tmp_path / "seed")
    seed.write_seed(dest, marketplace_dir=str(marketplace), copy=True)
    seed.write_seed(dest, marketplace_dir=str(marketplace))
    assert os.path.islink(os.path.join(dest, "marketplaces", "scitech"))

    seed.write_seed(dest, marketplace_dir=str(marketplace), copy=True)
    assert not os.path.islink(os.path.join(dest, "marketplaces", "scitech"))


def test_marketplace_resolution_prefers_site_then_user(monkeypatch, tmp_path):
    """A container owns /opt; an OOD deployment does not, so a per-user path
    has to resolve too."""
    from knowledge import layout

    monkeypatch.delenv("KNOWLEDGE_MARKETPLACE", raising=False)
    site = tmp_path / "opt"
    user = tmp_path / "home" / ".local" / "share" / "pegasus-ai" / "marketplace"
    monkeypatch.setattr(layout, "SITE_MARKETPLACE", str(site))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "home" / ".local" / "share"))

    # Nothing present: fall back to the site path, so a creator gets the
    # conventional location rather than a surprise.
    assert layout.marketplace_root() == str(site)

    # Only a user checkout (the OOD case).
    (user / ".claude-plugin").mkdir(parents=True)
    assert layout.marketplace_root() == str(user)

    # A site checkout wins once present (vetted, offline, admin-managed).
    (site / ".claude-plugin").mkdir(parents=True)
    assert layout.marketplace_root() == str(site)

    # An explicit override beats both.
    monkeypatch.setenv("KNOWLEDGE_MARKETPLACE", "/somewhere/else")
    assert layout.marketplace_root() == "/somewhere/else"


def test_seed_follows_the_resolver(monkeypatch, tmp_path, marketplace):
    """seed and knowledge_root must never resolve to different trees.

    A constant here once meant Claude Code could be pointed at one checkout
    while every other tool read another — the split this design exists to end.
    """
    from knowledge import layout, seed as seed_mod

    monkeypatch.setenv("KNOWLEDGE_MARKETPLACE", str(marketplace))
    dest = seed_mod.write_seed(str(tmp_path / "seed"))
    entry = json.loads(
        (tmp_path / "seed" / "known_marketplaces.json").read_text()
    )["scitech"]
    assert entry["source"]["path"] == str(marketplace.resolve())
    assert layout.marketplace_root() == str(marketplace)
    assert dest
