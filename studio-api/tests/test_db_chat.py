"""Tests for the startup prunes of the chat_messages and workflow_runs tables."""

from __future__ import annotations

import asyncio

from db import Database


def _run(coro):
    return asyncio.run(coro)


def test_prune_caps_rows(tmp_db):
    async def go():
        db = Database(db_path=tmp_db)
        await db.connect()
        try:
            # Nothing writes this table any more (#664); rows left by the
            # removed builtin engine are what the prune still bounds.
            for i in range(20):
                await db.db.execute(
                    "INSERT INTO chat_messages (conversation_id, role, content,"
                    " created_at) VALUES (?, ?, ?, ?)",
                    ("C", "user", f"m{i}", "2026-01-01T00:00:00"),
                )
            await db.db.commit()
            deleted = await db.prune_chat_messages(max_rows=5)
            async with db.db.execute(
                "SELECT content FROM chat_messages ORDER BY id"
            ) as cur:
                remaining = [row[0] async for row in cur]
            return deleted, remaining
        finally:
            await db.close()

    deleted, remaining = _run(go())
    assert deleted == 15
    # The five most recent survive, oldest-first.
    assert remaining == ["m15", "m16", "m17", "m18", "m19"]


def test_prune_workflow_runs_caps_rows(tmp_db):
    async def go():
        db = Database(db_path=tmp_db)
        await db.connect()
        try:
            for i in range(10):
                await db.upsert_workflow_run(
                    run_id=f"r{i}",
                    name=f"wf{i}",
                    run_dir=f"/runs/{i}",
                    status="running",
                )
            deleted = await db.prune_workflow_runs(max_rows=3)
            remaining = await db.list_workflow_runs()
            return deleted, len(remaining)
        finally:
            await db.close()

    deleted, remaining = _run(go())
    assert deleted == 7
    assert remaining == 3
