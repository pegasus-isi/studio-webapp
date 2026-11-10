"""Workflow monitor — queries stampede DB and tails event files.

Provides job-level status and real-time events for active workflows.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator

import structlog

from services.event_reader import jobs as event_jobs
from services.event_reader import read_events
from services.event_reader import workflow_detail as event_detail
from services.procutil import communicate_or_kill
from services.workflow_scanner import dagman_exit_status, get_run_status_async

log = structlog.get_logger()


async def get_workflow_detail(run_dir: str) -> dict[str, Any]:
    """Get workflow-level statistics from the workflow-monitor JSONL log.

    Returns total/completed/failed/running job counts and overall status.
    Falls back to a coarse status (no counts) when no JSONL exists yet.
    """
    loop = asyncio.get_event_loop()
    events = await loop.run_in_executor(None, read_events, run_dir)
    if not events:
        # Off-load the fallback too (BE-1): get_run_status shells out to
        # pegasus-status (15s timeout) and would otherwise block the loop right
        # after we correctly off-loaded read_events above.
        return {
            "status": await get_run_status_async(run_dir),
            "total_jobs": 0,
            "completed_jobs": 0,
            "failed_jobs": 0,
            "running_jobs": 0,
            "queued_jobs": 0,
            "exec_site": None,
        }
    detail = event_detail(events)
    if detail.get("status") == "running":
        # Same zombie guard as get_run_status (#296): an abnormal DAGMan death
        # writes no end event, so the stream reads "running" forever. This is
        # the path the runs list actually flows through (GET /api/workflows →
        # here, not get_run_status), so both need the cross-check. Job counts
        # stay as the events reported them — jobs mid-flight when DAGMan died
        # genuinely never finished.
        terminal = await loop.run_in_executor(None, dagman_exit_status, run_dir)
        if terminal:
            detail["status"] = terminal
    return detail


async def get_workflow_jobs(run_dir: str) -> list[dict[str, Any]]:
    """Get job-level detail for a run.

    Jobs come from the event log (JSONL or stampede fallback). The integrated
    monitord's JSONL job_state events omit site/duration/stderr, so enrich each
    job from the stampede DB — which always has them — filling only fields the
    events didn't provide.
    """
    from services.stampede_reader import read_stampede_job_details

    loop = asyncio.get_event_loop()
    events = await loop.run_in_executor(None, read_events, run_dir)
    jobs = event_jobs(events)

    details = await loop.run_in_executor(None, read_stampede_job_details, run_dir)
    if details:
        for j in jobs:
            d = details.get(j.get("job_id"))
            if not d:
                continue
            for key in ("site", "duration", "stderr", "transformation", "maxrss", "host"):
                if j.get(key) in (None, "") and d.get(key) is not None:
                    j[key] = d[key]
    return jobs


async def tail_workflow_events(
    run_dir: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield a workflow_state snapshot whenever the run's counts change.

    Every event source (the stampede DB, or a legacy workflow-events.jsonl /
    wfevents.jsonl) goes through get_workflow_detail, the same summary the runs list
    polls. Tailing workflow-events.jsonl directly used to forward its raw
    per-job events, which carry no counts, so the page's "running" read 0;
    and the file was looked for once, at connect, so one that appeared later
    was never followed.
    """
    async for event in _poll_detail(run_dir):
        yield event


_POLL_INTERVAL_S = 2.0
_POLL_MAX_SECONDS = 3600  # cap polling at 1 hour
_POLL_MAX_ITERS = int(_POLL_MAX_SECONDS / _POLL_INTERVAL_S)


async def _poll_detail(run_dir: str) -> AsyncGenerator[dict[str, Any], None]:
    """Poll the run's summary and yield it when it changes.

    Capped so an abandoned tab does not poll forever. The page reconnects
    when the stream ends while the run is still going.
    """
    prev_stats: dict[str, Any] = {}

    for _ in range(_POLL_MAX_ITERS):  # ~1 hour at _POLL_INTERVAL_S
        stats = await get_workflow_detail(run_dir)

        # Emit event if anything changed
        if stats != prev_stats:
            yield {
                "type": "workflow_state",
                "status": stats.get("status"),
                "total_jobs": stats.get("total_jobs", 0),
                "completed_jobs": stats.get("completed_jobs", 0),
                "failed_jobs": stats.get("failed_jobs", 0),
                "running_jobs": stats.get("running_jobs", 0),
                "queued_jobs": stats.get("queued_jobs", 0),
            }
            prev_stats = stats

            # Stop if workflow is done
            if stats.get("status") in ("succeeded", "failed"):
                yield {"type": "workflow_complete", "status": stats["status"]}
                return

        await asyncio.sleep(_POLL_INTERVAL_S)


async def run_pegasus_analyzer(run_dir: str) -> str:
    """Run pegasus-analyzer on a workflow run directory."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pegasus-analyzer", run_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await communicate_or_kill(proc, 60)
        output = stdout.decode("utf-8", errors="replace")
        if stderr:
            output += "\n" + stderr.decode("utf-8", errors="replace")
        return output
    except FileNotFoundError:
        return "pegasus-analyzer not found"
    except asyncio.TimeoutError:
        return "pegasus-analyzer timed out"
    except Exception as e:
        return f"Error running pegasus-analyzer: {e}"
