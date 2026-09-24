"""The one-time #403 migration that clears a stale `chat_engine` preference.

Flipping the model default to "opencode" does not move an existing user on its
own: `PUT /api/settings/ui` is wholesale, so anyone who ever saved a preference
has a `chat_engine` stored, and while "builtin" was the default that is the
value they got. The migration drops exactly that leftover.

What it must *not* do is outlive its one run. `_migrate` executes on every
connect, so an unguarded version would re-clear the preference of a user who
had deliberately gone back to Built-in, every time their pod restarted.
"""

from __future__ import annotations

import asyncio

from db import Database


def _run(coro):
    return asyncio.run(coro)


def _connected(tmp_db):
    return Database(db_path=tmp_db)


async def _seed_pre_upgrade(tmp_db, prefs):
    """A DB as it looked before this release: preferences set, no marker.

    Connecting at all runs `_migrate`, which on a fresh DB writes the marker
    immediately — so a test that just connected and then stored preferences
    would be seeding a DB the migration had already finished with, and would
    pass no matter what the migration did.
    """
    db = _connected(tmp_db)
    await db.connect()
    try:
        await db.set_setting("ui_preferences", prefs)
        from db import _CHAT_ENGINE_MIGRATION_KEY

        await db.db.execute(
            "DELETE FROM app_settings WHERE key = ?", (_CHAT_ENGINE_MIGRATION_KEY,)
        )
        await db.db.commit()
    finally:
        await db.close()


def test_stale_builtin_is_cleared_so_the_new_default_applies(tmp_db):
    async def go():
        # A user who saved some unrelated toggle: the wholesale PUT wrote the
        # engine too, at the then-current default.
        await _seed_pre_upgrade(
            tmp_db, {"chat_engine": "builtin", "skip_welcome": True}
        )

        # Next start runs the migration.
        db = _connected(tmp_db)
        await db.connect()
        try:
            return await db.get_setting("ui_preferences")
        finally:
            await db.close()

    prefs = _run(go())
    assert "chat_engine" not in prefs, "the stale engine should be gone"
    # Everything beside it survives — this clears one key, it is not a reset.
    assert prefs["skip_welcome"] is True


def test_a_deliberate_builtin_survives_later_restarts(tmp_db):
    """The guard. Without it this is a preference that will not stay set."""

    async def go():
        await _seed_pre_upgrade(tmp_db, {"chat_engine": "builtin"})

        # First restart: the migration runs and clears the leftover.
        db = _connected(tmp_db)
        await db.connect()
        try:
            cleared = await db.get_setting("ui_preferences")
            # The user now goes to Settings and picks Built-in on purpose.
            await db.set_setting("ui_preferences", {"chat_engine": "builtin"})
        finally:
            await db.close()

        # Second restart: their choice must still be there.
        db = _connected(tmp_db)
        await db.connect()
        try:
            return cleared, await db.get_setting("ui_preferences")
        finally:
            await db.close()

    cleared, after = _run(go())
    assert "chat_engine" not in cleared
    assert after["chat_engine"] == "builtin", "a deliberate choice was re-cleared"


def test_an_explicit_opencode_is_left_alone(tmp_db):
    async def go():
        await _seed_pre_upgrade(tmp_db, {"chat_engine": "opencode"})

        db = _connected(tmp_db)
        await db.connect()
        try:
            return await db.get_setting("ui_preferences")
        finally:
            await db.close()

    assert _run(go())["chat_engine"] == "opencode"


def test_a_corrupt_preferences_blob_is_left_exactly_as_found(tmp_db):
    """Not this migration's business — and it must not crash the connect."""

    async def go():
        await _seed_pre_upgrade(tmp_db, "not-a-dict")

        db = _connected(tmp_db)
        await db.connect()
        try:
            return await db.get_setting("ui_preferences")
        finally:
            await db.close()

    assert _run(go()) == "not-a-dict"


def test_no_preferences_row_still_marks_the_migration_done(tmp_db):
    """Otherwise it re-runs on every start until the user saves something."""

    async def go():
        db = _connected(tmp_db)
        await db.connect()
        try:
            pass  # fresh DB, nothing stored
        finally:
            await db.close()

        db = _connected(tmp_db)
        await db.connect()
        try:
            from db import _CHAT_ENGINE_MIGRATION_KEY

            return await db.get_setting(_CHAT_ENGINE_MIGRATION_KEY)
        finally:
            await db.close()

    assert _run(go()) is True
