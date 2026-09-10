"""Health check endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter

from models import DetailedHealthResponse, HealthResponse
from services import submit_host

log = structlog.get_logger()

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    from main import __release_channel__, __version__

    return HealthResponse(
        status="ok", version=__version__, channel=__release_channel__
    )


@router.get("/detailed", response_model=DetailedHealthResponse)
async def detailed_health() -> DetailedHealthResponse:
    from main import __version__, db

    # Check DB
    db_ok = False
    try:
        async with db.db.execute("SELECT 1") as cursor:
            await cursor.fetchone()
        db_ok = True
    except Exception:
        pass

    # Check Pegasus version
    pegasus_version = await _run_version_cmd("pegasus-version")

    # Check HTCondor version
    condor_version = await _run_version_cmd("condor_version")

    return DetailedHealthResponse(
        status="ok",
        version=__version__,
        db_ok=db_ok,
        pegasus_version=pegasus_version,
        condor_version=condor_version,
    )


@router.get("/submit-host")
async def submit_host_compat() -> dict[str, object]:
    """Whether this workspace's Pegasus matches the submit host's.

    Exposed separately from /detailed because it is the answer to "why did
    every workflow fail on its first step", and because it is the one health
    fact that depends on another machine. Cheap: reads schedd ClassAds, never
    submits a job. See services/submit_host.py for why this exists.
    """
    return (await submit_host.check()).as_dict()


async def _run_version_cmd(cmd: str) -> str | None:
    """First line of a version command's output, or None.

    Runs through the active compute backend so the dashboard reports the
    stack workflows actually execute on — a remote backend answers with the
    remote host's versions.
    """
    try:
        from services.backends import active_backend

        result = await active_backend().exec([cmd], timeout=10)
        if result.returncode == 0 and result.stdout:
            return result.stdout.decode().strip().split("\n")[0]
    except Exception:
        pass
    return None
