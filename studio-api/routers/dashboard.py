"""Dashboard endpoint — one aggregate call for the landing page.

Composes what the individual routers already expose (projects, runs,
resources, health, unread notifications) so the landing page renders with a
single round trip.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter

from routers.health import _run_version_cmd
from services import condor_pool
from services.workflow_monitor import get_workflow_detail
from services.workflow_scanner import discover_projects, discover_runs

log = structlog.get_logger()

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_RECENT_RUNS = 6


@router.get("")
async def dashboard() -> dict:
    """Workflows summary, recent runs, resource snapshot, and versions."""
    from main import db

    loop = asyncio.get_event_loop()
    projects = await loop.run_in_executor(None, discover_projects)
    runs = await loop.run_in_executor(None, discover_runs)

    run_rows: list[dict] = []
    by_status: dict[str, int] = {}
    for run in runs:
        detail = await get_workflow_detail(run["run_dir"])
        status = detail.get("status") or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
        run_rows.append({
            "run_id": run["run_id"],
            "run_name": run.get("run_name", ""),
            "name": run.get("name", ""),
            "status": status,
            "total_jobs": detail.get("total_jobs", 0),
            "completed_jobs": detail.get("completed_jobs", 0),
            "failed_jobs": detail.get("failed_jobs", 0),
            "timestamp": run.get("timestamp"),
        })

    # Newest first; braindump timestamps are sortable strings, missing last.
    run_rows.sort(key=lambda r: str(r.get("timestamp") or ""), reverse=True)

    project_status: dict[str, int] = {}
    for p in projects:
        project_status[p["status"]] = project_status.get(p["status"], 0) + 1

    resources = await condor_pool.get_status()
    pegasus_version, condor_version = await asyncio.gather(
        _run_version_cmd("pegasus-version"),
        _run_version_cmd("condor_version"),
    )

    try:
        unread = await db.unread_notification_count()
    except Exception:
        unread = 0

    return {
        "projects": {"total": len(projects), "by_status": project_status},
        "runs": {
            "total": len(run_rows),
            "by_status": by_status,
            "recent": run_rows[:_RECENT_RUNS],
        },
        "resources": resources,
        "versions": {"pegasus": pegasus_version, "condor": condor_version},
        "notifications": {"unread": unread},
    }
