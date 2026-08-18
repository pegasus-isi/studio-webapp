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


def test_marketplace_resolution_prefers_the_user_checkout(monkeypatch, tmp_path):
    """The user's own checkout wins, in the container as much as on OOD.

    A root-owned tree under /opt can only be changed by rebuilding the image,
    and any `git pull` into it is undone by the next pod restart.
    """
    from knowledge import layout

    monkeypatch.delenv("KNOWLEDGE_MARKETPLACE", raising=False)
    seed_dir = tmp_path / "opt"
    user = tmp_path / "home" / ".local" / "share" / "pegasus-ai" / "marketplace"
    monkeypatch.setattr(layout, "SITE_MARKETPLACE", str(seed_dir))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "home" / ".local" / "share"))

    # Nothing yet: name the user path, so whoever creates it uses the right one.
    assert layout.marketplace_root() == str(user)

    # Only the image seed (before first launch clones it).
    (seed_dir / ".claude-plugin").mkdir(parents=True)
    assert layout.marketplace_root() == str(seed_dir)

    # Once the user has their own copy it wins, even though the seed remains.
    (user / ".claude-plugin").mkdir(parents=True)
    assert layout.marketplace_root() == str(user)

    # An explicit override still beats both (a site-staged, read-only copy).
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


def _init_repo(path):
    import subprocess
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
        # The developer's global config may set commit.gpgsign; signing here
        # would depend on an agent holding a passphrase, so the suite would
        # pass or fail based on whether someone had recently signed something.
        ["config", "commit.gpgsign", "false"],
        ["add", "-A"],
        ["commit", "-q", "-m", "seed"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True,
                       capture_output=True)


def test_checkout_status_reports_the_sha(monkeypatch, tmp_path, marketplace):
    """The SHA is the version — there is no package number to fall back on."""
    from knowledge import checkout

    _init_repo(marketplace)
    monkeypatch.setenv("KNOWLEDGE_MARKETPLACE", str(marketplace))

    info = checkout.status()
    assert info["exists"] and info["is_git"]
    assert len(info["sha"]) == 40
    assert info["subject"] == "seed"
    assert info["dirty"] is False


def test_update_refuses_to_discard_local_edits(monkeypatch, tmp_path, marketplace):
    """A user who edited a skill meant it; being out of date is the lesser harm."""
    from knowledge import checkout

    _init_repo(marketplace)
    monkeypatch.setenv("KNOWLEDGE_MARKETPLACE", str(marketplace))
    (marketplace / ".claude-plugin" / "marketplace.json").write_text('{"name":"scitech","plugins":[]}\n  ')

    result = checkout.update()
    assert result["updated"] is False
    assert result["reason"] == "local modifications"
    assert result["dirty"] is True

    # force still refuses: uncommitted edits are the one unrecoverable thing.
    assert checkout.update(force=True)["updated"] is False


def test_update_is_a_noop_without_git(monkeypatch, tmp_path):
    from knowledge import checkout

    plain = tmp_path / "plain"
    (plain / ".claude-plugin").mkdir(parents=True)
    monkeypatch.setenv("KNOWLEDGE_MARKETPLACE", str(plain))

    result = checkout.update()
    assert result["updated"] is False
    assert result["reason"] == "not a git checkout"
