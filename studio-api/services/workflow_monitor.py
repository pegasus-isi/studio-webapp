"""Workflow monitor — queries stampede DB and tails event files.

Provides job-level status and real-time events for active workflows.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

import structlog

from services.backends import backend_for_path
from services.event_reader import find_events_file
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
            for key in ("site", "duration", "stderr", "transformation", "maxrss"):
                if j.get(key) in (None, "") and d.get(key) is not None:
                    j[key] = d[key]
    return jobs


async def tail_workflow_events(
    run_dir: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Tail workflow event files and yield events as dicts.

    Order: a live stream from the active backend (a remote backend tails the
    JSONL where monitord writes it, skipping mirror-sync latency), then any
    accepted local JSONL, then polling the stampede DB. Tailing a *mirrored*
    JSONL directly would be wrong anyway: rsync replaces the file by rename,
    so a held-open tail keeps reading the dead inode.
    """
    from services.backends import backend_for_path

    stream = await backend_for_path(run_dir).stream_events(run_dir)
    if stream is not None:
        async for event in stream:
            yield event
        return

    events_file = find_events_file(run_dir)
    if events_file:
        # Tail the JSONL file
        async for event in _tail_jsonl(events_file):
            yield event
    else:
        # Poll stampede DB for changes
        async for event in _poll_stampede(run_dir):
            yield event


async def _tail_jsonl(path: str) -> AsyncGenerator[dict[str, Any], None]:
    """Tail a JSONL file, yielding new lines as they appear."""
    try:
        with open(path) as f:
            # Seek to end
            f.seek(0, 2)

            while True:
                line = f.readline()
                if line:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            pass
                else:
                    await asyncio.sleep(1.0)
    except Exception as e:
        log.warning("tail_jsonl_failed", path=path, error=str(e))
        yield {"type": "error", "message": str(e)}


_POLL_INTERVAL_S = 2.0
_POLL_MAX_SECONDS = 3600  # cap polling at 1 hour
_POLL_MAX_ITERS = int(_POLL_MAX_SECONDS / _POLL_INTERVAL_S)


async def _poll_stampede(run_dir: str) -> AsyncGenerator[dict[str, Any], None]:
    """Poll the stampede DB periodically and yield state-change events."""
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
        result = await backend_for_path(run_dir).exec(
            ["pegasus-analyzer", run_dir], timeout=60
        )
        output = result.stdout.decode("utf-8", errors="replace")
        if result.stderr:
            output += "\n" + result.stderr.decode("utf-8", errors="replace")
        return output
    except FileNotFoundError:
        return "pegasus-analyzer not found"
    except asyncio.TimeoutError:
        return "pegasus-analyzer timed out"
    except Exception as e:
        return f"Error running pegasus-analyzer: {e}"
