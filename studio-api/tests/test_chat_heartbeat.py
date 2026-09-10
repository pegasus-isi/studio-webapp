"""Tests for the in-round SSE heartbeat.

A reasoning model can spend minutes on thinking tokens before the first text
delta of a round. The per-round ping fires *before* the round, so it does not
help inside one: a proxy sees no bytes for the whole think and drops the
connection, which reaches the client as a bare network error.
"""

from __future__ import annotations

import asyncio

import routers.chat as chat


def _drain(agen, interval):
    async def run():
        return [ev async for ev in chat._with_heartbeat(agen, interval)]

    return asyncio.run(run())


async def _quiet_then_one(delay: float):
    await asyncio.sleep(delay)
    yield {"type": "text", "text": "hi"}


async def _immediate():
    yield {"type": "text", "text": "a"}
    yield {"type": "result", "result": {"content": "a"}}


async def _never_yields():
    return
    yield  # pragma: no cover — makes this an async generator


def test_silence_produces_heartbeats_then_the_event():
    events = _drain(_quiet_then_one(0.25), interval=0.05)
    assert events[-1] == {"type": "text", "text": "hi"}
    assert all(e is chat._HEARTBEAT for e in events[:-1])
    assert len(events) > 1, "a quiet stream must emit at least one heartbeat"


def test_a_prompt_stream_gets_no_heartbeats():
    assert _drain(_immediate(), interval=5.0) == [
        {"type": "text", "text": "a"},
        {"type": "result", "result": {"content": "a"}},
    ]


def test_empty_stream_terminates():
    assert _drain(_never_yields(), interval=5.0) == []


def test_events_are_not_consumed_by_the_heartbeat_wait():
    """The bounded wait must not drop the event it was waiting for."""
    async def three():
        for i in range(3):
            await asyncio.sleep(0.06)
            yield {"type": "text", "text": str(i)}

    events = [e for e in _drain(three(), interval=0.02) if e is not chat._HEARTBEAT]
    assert [e["text"] for e in events] == ["0", "1", "2"]


def test_breaking_out_early_cancels_the_pending_read():
    """A caller that stops iterating must not leave __anext__ in flight."""
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(30)
        yield {"type": "text", "text": "never"}

    async def run():
        agen = chat._with_heartbeat(slow(), interval=0.01)
        async for ev in agen:
            if ev is chat._HEARTBEAT:
                break
        await agen.aclose()
        # cancel() only *requests* cancellation; give the loop a tick to reap.
        await asyncio.sleep(0)
        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        # Nothing may still be runnable — a live __anext__ would outlive the request.
        return [t for t in leftover if not (t.cancelled() or t.done())]

    assert asyncio.run(run()) == []
