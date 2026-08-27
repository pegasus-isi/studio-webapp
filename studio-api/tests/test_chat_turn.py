"""Tests for the chat turn broker (services/chat_turn.py).

The point of the broker is that a turn stops belonging to the request that
started it, so the properties worth pinning are the ones that only matter once
the client is gone: the generator still runs to completion, and a client that
comes back sees the whole turn rather than the tail of it.
"""

from __future__ import annotations

import asyncio

import pytest

from services import chat_turn


async def _gen(chunks, delay=0.0):
    for c in chunks:
        if delay:
            await asyncio.sleep(delay)
        yield c


def test_a_turn_completes_with_nobody_listening():
    """The whole point: navigating away must not cancel the answer.

    The engine generators persist assistant messages themselves, so draining to
    completion is what puts the reply in the database for a user who left.
    """

    async def scenario():
        drained = []

        async def source():
            for c in ["a", "b", "c"]:
                await asyncio.sleep(0)
                drained.append(c)
                yield c

        manager = chat_turn.ChatTurnManager()
        turn = manager.start(source(), "conv1")
        # Never subscribe — simulate a client that navigated away immediately.
        await asyncio.wait_for(turn._task, timeout=2)
        return drained, turn

    drained, turn = asyncio.run(scenario())
    assert drained == ["a", "b", "c"]
    assert turn.done is True


def test_a_late_subscriber_replays_the_whole_turn():
    async def scenario():
        manager = chat_turn.ChatTurnManager()
        turn = manager.start(_gen(["one", "two", "three"]), "conv1")
        await asyncio.wait_for(turn._task, timeout=2)
        # Attach only after it finished — a page load mid-answer that landed
        # just as the turn ended.
        return [c async for c in turn.subscribe()]

    assert asyncio.run(scenario()) == ["one", "two", "three"]


def test_a_subscriber_sees_chunks_emitted_after_it_attaches():
    async def scenario():
        turn = chat_turn.ChatTurn("conv1")
        received = []

        async def consume():
            async for chunk in turn.subscribe():
                received.append(chunk)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)  # let it register
        turn._emit("live-1")
        turn._emit("live-2")
        turn._finish()
        await asyncio.wait_for(task, timeout=2)
        return received

    assert asyncio.run(scenario()) == ["live-1", "live-2"]


def test_replay_and_live_do_not_duplicate():
    # A chunk emitted between registering and snapshotting the backlog must
    # appear exactly once — the reason those two steps have no await between
    # them.
    async def scenario():
        turn = chat_turn.ChatTurn("conv1")
        turn._emit("backlog")
        received = []

        async def consume():
            async for chunk in turn.subscribe():
                received.append(chunk)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        turn._emit("live")
        turn._finish()
        await asyncio.wait_for(task, timeout=2)
        return received

    assert asyncio.run(scenario()) == ["backlog", "live"]


def test_a_failing_generator_still_terminates_the_turn():
    # A turn that never finishes leaves every subscriber hanging forever.
    async def scenario():
        async def broken():
            yield "partial"
            raise RuntimeError("engine blew up")

        manager = chat_turn.ChatTurnManager()
        turn = manager.start(broken(), "conv1")
        await asyncio.wait_for(turn._task, timeout=2)
        return turn, [c async for c in turn.subscribe()]

    turn, chunks = asyncio.run(scenario())
    assert turn.done is True
    assert chunks[0] == "partial"
    # The user is told, rather than the message simply stopping.
    assert any("stopped unexpectedly" in c for c in chunks)


def test_starting_a_new_turn_cancels_the_abandoned_one():
    # Its output can no longer reach anyone; leaving it draining would burn a
    # provider call and hold an httpx client open.
    async def scenario():
        manager = chat_turn.ChatTurnManager()
        first = manager.start(_gen(["x"] * 50, delay=0.05), "conv1")
        await asyncio.sleep(0)
        second = manager.start(_gen(["new"]), "conv1")
        await asyncio.sleep(0.05)
        return first, second

    first, second = asyncio.run(scenario())
    assert first._task.cancelled() or first._task.done()
    assert second.turn_id != first.turn_id


def test_manager_reports_no_active_turn_before_anything_starts():
    assert chat_turn.ChatTurnManager().active() is None


def test_finished_turns_expire_so_a_stale_one_is_not_reattached(monkeypatch):
    async def scenario():
        manager = chat_turn.ChatTurnManager()
        turn = manager.start(_gen(["done"]), "conv1")
        await asyncio.wait_for(turn._task, timeout=2)
        # Still attachable immediately after finishing…
        assert manager.current() is not None
        # …but not forever: after that the reply is in history, which is the
        # right source for it.
        turn.finished_at -= chat_turn._RETAIN_SECONDS + 1
        return manager.current()

    assert asyncio.run(scenario()) is None


# ── endpoints ─────────────────────────────────────────────────────────────


def test_turn_endpoint_reports_inactive_when_nothing_is_running(client):
    assert client.get("/api/chat/turn").json() == {"active": False}


def test_turn_events_404s_when_there_is_no_turn(client):
    assert client.get("/api/chat/turn/events").status_code == 404
