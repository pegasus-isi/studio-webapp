"""JupyterLab status endpoints.

JupyterLab runs as an always-on s6 service (docker/s6-overlay/s6-rc.d/jupyter),
started with the container — like the official jupyter image. The studio does
NOT launch or manage the process; these endpoints only report status, and the
frontend opens the running instance at /jupyter/lab in a new browser tab.
"""

from __future__ import annotations

import socket

import structlog
from fastapi import APIRouter

from models import JupyterStatusResponse

log = structlog.get_logger()

router = APIRouter(prefix="/api/jupyter", tags=["jupyter"])

_JUPYTER_PORT = 8889


def _port_in_use(port: int = _JUPYTER_PORT) -> bool:
    """True if the JupyterLab server is listening on its port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


@router.get("/status", response_model=JupyterStatusResponse)
async def jupyter_status() -> JupyterStatusResponse:
    """Report whether the JupyterLab service is up (by port)."""
    running = _port_in_use()
    return JupyterStatusResponse(
        status="running" if running else "stopped",
        port=_JUPYTER_PORT if running else None,
    )


# --- Back-compat no-ops -----------------------------------------------------
# JupyterLab is an always-on s6 service, so the studio neither starts nor stops
# it. These endpoints remain for older clients and simply report status.


@router.post("/start", response_model=JupyterStatusResponse)
async def start_jupyter() -> JupyterStatusResponse:
    """No-op: JupyterLab is managed by s6. Returns current status."""
    return await jupyter_status()


@router.post("/stop", response_model=JupyterStatusResponse)
async def stop_jupyter() -> JupyterStatusResponse:
    """No-op: JupyterLab is managed by s6 and not stopped by the studio."""
    return await jupyter_status()
