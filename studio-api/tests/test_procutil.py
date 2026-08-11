"""Tests for communicate_or_kill — the BE-2 fix.

asyncio.wait_for cancels the wait, not the child: before this helper a
timed-out `pegasus-plan --submit` kept running detached and could submit a
second DAG after the API had already reported "timed out".
"""

from __future__ import annotations

import asyncio

import pytest

from services.procutil import communicate_or_kill


def test_timeout_kills_the_child():
    """On timeout the child must be dead and reaped, not left running."""

    async def go():
        proc = await asyncio.create_subprocess_exec(
            "sleep", "30",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        with pytest.raises(asyncio.TimeoutError):
            await communicate_or_kill(proc, 0.2)
        return proc.returncode

    rc = asyncio.run(go())
    # returncode is None while the child is still alive; a killed child
    # reports a negative signal code (-9 for SIGKILL).
    assert rc is not None
    assert rc < 0


def test_success_passes_output_through():
    async def go():
        proc = await asyncio.create_subprocess_exec(
            "echo", "hi",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return await communicate_or_kill(proc, 10)

    stdout, stderr = asyncio.run(go())
    assert stdout.strip() == b"hi"
    assert stderr == b""


def test_child_exiting_during_kill_window_still_raises():
    """If the child dies between the cancelled wait and the kill, the
    TimeoutError must still propagate (ProcessLookupError suppressed)."""

    async def go():
        proc = await asyncio.create_subprocess_exec(
            "sleep", "30",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Simulate the race: the child is already gone by kill time.
        # timeout=0 makes wait_for raise deterministically even though the
        # dead child's communicate() would complete on the next loop tick.
        proc.kill()
        await proc.wait()
        with pytest.raises(asyncio.TimeoutError):
            await communicate_or_kill(proc, 0)

    asyncio.run(go())
