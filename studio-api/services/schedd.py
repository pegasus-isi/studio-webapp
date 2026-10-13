"""Ask the schedd to start what was just submitted, instead of waiting.

The schedd starts scheduler-universe jobs (DAGMan, so every Pegasus workflow)
and local-universe jobs (Studio's remote planning, #488) from its periodic
timer, ``SCHEDD_INTERVAL``, 300 s by default, not when they are submitted.
``condor_submit`` sends a reschedule of its own (``SUBMIT_SEND_RESCHEDULE``),
but on the FABRIC Open OnDemand test cluster a just-submitted DAGMan job still
sat idle for 178 s: the schedd does not run its cycle more often than
``SCHEDD_MIN_INTERVAL`` (5 s), and a reschedule that lands inside that window
is absorbed into the cycle that just ran. An explicit reschedule after the
window started it at once (#494).

So: one reschedule now and one just after the window, in the background,
best effort. It is the same command a user can type, at the same
authorization level as ``condor_submit`` itself (verified as an ordinary user
with only an IDTOKEN), so it needs nothing an HPC site's users do not already
have. Nothing here is required for correctness: if it fails, the timer still
starts the job.
"""

from __future__ import annotations

import asyncio

import structlog

log = structlog.get_logger()

# Just past the schedd's default SCHEDD_MIN_INTERVAL of 5 s.
_SECOND_NUDGE_AFTER_S = 6.0

# Strong references: a task nobody holds can be garbage-collected mid-flight.
_pending: set[asyncio.Task[None]] = set()


async def reschedule() -> bool:
    """Send one ``condor_reschedule``. True if the schedd accepted it."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "condor_reschedule",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=30)
    except (OSError, asyncio.TimeoutError) as e:
        log.info("schedd_reschedule_failed", error=str(e)[:200])
        return False
    if proc.returncode != 0:
        log.info(
            "schedd_reschedule_failed",
            error=err.decode("utf-8", errors="replace").strip()[:200],
        )
        return False
    return True


async def _nudge(second_after: float) -> None:
    await reschedule()
    await asyncio.sleep(second_after)
    await reschedule()


def nudge(second_after: float = _SECOND_NUDGE_AFTER_S) -> None:
    """Reschedule now and again after ``second_after`` s, without waiting.

    Call right after a successful submit. Never raises; a caller outside a
    running event loop gets nothing rather than an error.
    """
    try:
        task = asyncio.get_running_loop().create_task(_nudge(second_after))
    except RuntimeError:
        return
    _pending.add(task)
    task.add_done_callback(_pending.discard)
