"""Subprocess helpers shared across services and routers."""

from __future__ import annotations

import asyncio
import contextlib


async def communicate_or_kill(
    proc: asyncio.subprocess.Process, timeout: float
) -> tuple[bytes, bytes]:
    """``proc.communicate()`` with a timeout that actually stops the child.

    ``asyncio.wait_for`` cancels the *wait*, not the subprocess: a timed-out
    ``pegasus-plan --submit`` kept running detached and could submit a second
    DAG after the API had already reported "timed out" (and every timed-out
    generator introspection leaked a python3). Kill the child before
    re-raising, so a caller's ``except TimeoutError`` means "the work was
    stopped" — never "the work is now unsupervised".
    """
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        # ProcessLookupError: the child exited in the gap between the
        # cancellation and the kill — nothing left to stop.
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        # Reap so the killed child doesn't linger as a zombie for the life
        # of the server process.
        with contextlib.suppress(Exception):
            await proc.wait()
        raise
