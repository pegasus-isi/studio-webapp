"""Tests for 👍/👎 ratings on OpenCode assistant replies (`/api/feedback`)."""

from __future__ import annotations

import asyncio

import pytest

from db import Database

URL = "/api/feedback/message"


def _row(client, session_id: str = "ses_1", message_id: str = "msg_1"):
    """Read the stored rating through the app's own db, on the client's loop.

    `main.db` is bound to the TestClient's event loop, so it can't be awaited
    with a fresh `asyncio.run`; hop onto the portal the client already runs.
    """
    import main

    return client.portal.call(main.db.get_message_feedback, session_id, message_id)


def _count(client) -> int:
    import main

    async def go():
        async with main.db.db.execute("SELECT COUNT(*) FROM message_feedback") as c:
            return (await c.fetchone())[0]

    return client.portal.call(go)


def test_rate_up_stores_row(client):
    res = client.post(
        URL,
        json={
            "session_id": "ses_1",
            "message_id": "msg_1",
            "conversation_id": "conv_a",
            "model": "renci/kimi-k2.6",
            "agent": "build",
            "rating": 1,
        },
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    row = _row(client)
    assert row is not None
    assert row["rating"] == 1
    assert row["conversation_id"] == "conv_a"
    assert row["model"] == "renci/kimi-k2.6"
    assert row["agent"] == "build"
    assert row["created_at"] and row["updated_at"]


def test_change_vote_updates_in_place(client):
    client.post(
        URL,
        json={"session_id": "ses_1", "message_id": "msg_1", "model": "m1", "rating": 1},
    )
    first = _row(client)

    # A changed vote, sent without the context the first one carried, must
    # neither add a row nor wipe the model it already knew.
    res = client.post(
        URL, json={"session_id": "ses_1", "message_id": "msg_1", "rating": -1}
    )
    assert res.status_code == 200

    row = _row(client)
    assert row["rating"] == -1
    assert row["model"] == "m1"
    assert row["id"] == first["id"]
    assert row["created_at"] == first["created_at"]
    assert _count(client) == 1


def test_same_message_id_in_other_session_is_separate(client):
    client.post(URL, json={"session_id": "ses_1", "message_id": "msg_1", "rating": 1})
    client.post(URL, json={"session_id": "ses_2", "message_id": "msg_1", "rating": -1})
    assert _row(client, "ses_1")["rating"] == 1
    assert _row(client, "ses_2")["rating"] == -1
    assert _count(client) == 2


def test_unrate_deletes_row(client):
    client.post(URL, json={"session_id": "ses_1", "message_id": "msg_1", "rating": 1})
    res = client.post(
        URL, json={"session_id": "ses_1", "message_id": "msg_1", "rating": 0}
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert _row(client) is None


def test_unrate_unknown_message_is_ok(client):
    # A retried un-rate whose first attempt already landed must not error.
    res = client.post(
        URL, json={"session_id": "ses_x", "message_id": "msg_x", "rating": 0}
    )
    assert res.status_code == 200
    assert _count(client) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"session_id": "s", "message_id": "m", "rating": 2},
        {"session_id": "s", "message_id": "m", "rating": -2},
        {"session_id": "s", "message_id": "m", "rating": "1"},
        {"session_id": "s", "message_id": "m", "rating": True},
        {"session_id": "s", "message_id": "m", "rating": 1.0},
        {"session_id": "s", "message_id": "m"},
        {"session_id": "s", "rating": 1},
        {"message_id": "m", "rating": 1},
        {"session_id": "", "message_id": "m", "rating": 1},
        {"session_id": "s", "message_id": "m", "rating": 1, "model": "x" * 1000},
    ],
)
def test_validation_rejects_bad_body(client, body):
    res = client.post(URL, json=body)
    assert res.status_code == 422
    assert _count(client) == 0


def test_db_upsert_and_clear(tmp_db):
    async def go():
        db = Database(db_path=tmp_db)
        await db.connect()
        try:
            await db.upsert_message_feedback("s", "m", 1, model="a")
            await db.upsert_message_feedback("s", "m", -1, model="b")
            row = await db.get_message_feedback("s", "m")
            cleared = await db.clear_message_feedback("s", "m")
            again = await db.clear_message_feedback("s", "m")
            gone = await db.get_message_feedback("s", "m")
            return row, cleared, again, gone
        finally:
            await db.close()

    row, cleared, again, gone = asyncio.run(go())
    assert row["rating"] == -1
    assert row["model"] == "b"
    assert cleared is True
    assert again is False
    assert gone is None
