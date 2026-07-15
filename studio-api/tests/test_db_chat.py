"""Tests for chat-message persistence: conversation scoping + pruning."""

from __future__ import annotations

import asyncio

from db import Database


def _run(coro):
    return asyncio.run(coro)


def test_conversation_scoped_history(tmp_db):
    async def go():
        db = Database(db_path=tmp_db)
        await db.connect()
        try:
            await db.add_chat_message("user", "a1", conversation_id="A")
            await db.add_chat_message("assistant", "a2", conversation_id="A")
            await db.add_chat_message("user", "b1", conversation_id="B")

            a = await db.get_chat_history(conversation_id="A")
            b = await db.get_chat_history(conversation_id="B")
            allm = await db.get_chat_history()
            return (
                [m["content"] for m in a],
                [m["content"] for m in b],
                len(allm),
            )
        finally:
            await db.close()

    a, b, total = _run(go())
    assert a == ["a1", "a2"]  # oldest-first
    assert b == ["b1"]
    assert total == 3


def test_prune_caps_rows(tmp_db):
    async def go():
        db = Database(db_path=tmp_db)
        await db.connect()
        try:
            for i in range(20):
                await db.add_chat_message("user", f"m{i}", conversation_id="C")
            deleted = await db.prune_chat_messages(max_rows=5)
            remaining = await db.get_chat_history(limit=100)
            return deleted, [m["content"] for m in remaining]
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


def test_set_chat_feedback(tmp_db):
    async def go():
        db = Database(db_path=tmp_db)
        await db.connect()
        try:
            mid = await db.add_chat_message(
                "assistant", "hello", conversation_id="A"
            )
            # Rate useful, then read it back via history.
            assert await db.set_chat_feedback(mid, 1) is True
            rows = await db.get_chat_history(conversation_id="A")
            useful = rows[0]["feedback"]
            # Flip to not-useful, then clear.
            await db.set_chat_feedback(mid, -1)
            rows2 = await db.get_chat_history(conversation_id="A")
            await db.set_chat_feedback(mid, None)
            rows3 = await db.get_chat_history(conversation_id="A")
            # Unknown id returns False.
            missing = await db.set_chat_feedback(999999, 1)
            return useful, rows2[0]["feedback"], rows3[0]["feedback"], missing
        finally:
            await db.close()

    useful, notuseful, cleared, missing = _run(go())
    assert useful == 1
    assert notuseful == -1
    assert cleared is None
    assert missing is False
