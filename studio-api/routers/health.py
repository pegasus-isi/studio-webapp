"""Health check endpoints."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter

from models import DetailedHealthResponse, HealthResponse

log = structlog.get_logger()

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    from main import __version__

    return HealthResponse(status="ok", version=__version__)


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


async def _run_version_cmd(cmd: str) -> str | None:
    """Run a version command and return first line of output, or None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0 and stdout:
            return stdout.decode().strip().split("\n")[0]
    except Exception:
        pass
    return None
