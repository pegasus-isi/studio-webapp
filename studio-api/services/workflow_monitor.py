"""Workflow monitor — queries stampede DB and tails event files.

Provides job-level status and real-time events for active workflows.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncGenerator

import structlog

from services.event_reader import jobs as event_jobs
from services.event_reader import read_events
from services.event_reader import workflow_detail as event_detail
from services.workflow_scanner import get_run_status

log = structlog.get_logger()


async def get_workflow_detail(run_dir: str) -> dict[str, Any]:
    """Get workflow-level statistics from the workflow-monitor JSONL log.

    Returns total/completed/failed/running job counts and overall status.
    Falls back to a coarse status (no counts) when no JSONL exists yet.
    """
    loop = asyncio.get_event_loop()
    events = await loop.run_in_executor(None, read_events, run_dir)
    if not events:
        return {
            "status": get_run_status(run_dir),
            "total_jobs": 0,
            "completed_jobs": 0,
            "failed_jobs": 0,
            "running_jobs": 0,
            "exec_site": None,
        }
    return event_detail(events)


async def get_workflow_jobs(run_dir: str) -> list[dict[str, Any]]:
    """Get job-level detail from the workflow-monitor JSONL log."""
    loop = asyncio.get_event_loop()
    events = await loop.run_in_executor(None, read_events, run_dir)
    return event_jobs(events)


async def tail_workflow_events(
    run_dir: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Tail workflow event files and yield events as dicts.

    Looks for workflow-events.jsonl (from workflow-monitor --server)
    or polls the stampede DB for state changes.
    """
    events_file = os.path.join(run_dir, "workflow-events.jsonl")

    if os.path.isfile(events_file):
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


async def _poll_stampede(run_dir: str) -> AsyncGenerator[dict[str, Any], None]:
    """Poll the stampede DB periodically and yield state-change events."""
    prev_stats: dict[str, Any] = {}

    for _ in range(3600):  # Max 1 hour of polling
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

        await asyncio.sleep(2.0)


async def run_pegasus_analyzer(run_dir: str) -> str:
    """Run pegasus-analyzer on a workflow run directory."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pegasus-analyzer", run_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
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
