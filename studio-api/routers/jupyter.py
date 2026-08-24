"""JupyterLab status endpoint.

JupyterLab runs as an always-on service under supervisord
(launcher/scripts/jupyter.sh), started with the container. The studio
does NOT launch or manage the process; this endpoint only reports status, and
the frontend opens the running instance at /jupyter/lab in a new browser tab.
"""

from __future__ import annotations

import os
import socket

import structlog
from fastapi import APIRouter

from models import JupyterStatusResponse

log = structlog.get_logger()

router = APIRouter(prefix="/api/jupyter", tags=["jupyter"])


def _jupyter_port() -> int:
    """The port jupyter.sh bound. The launcher allocates it per session on
    shared native hosts (a fixed 8889 is host-global there and collides
    between users), so read the env at call time — a module-level constant
    would freeze whatever the test environment had at import."""
    return int(os.environ.get("STUDIO_JUPYTER_PORT", "8889"))


def _port_in_use(port: int) -> bool:
    """True if the JupyterLab server is listening on its port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


@router.get("/status", response_model=JupyterStatusResponse)
async def jupyter_status() -> JupyterStatusResponse:
    """Report whether the JupyterLab service is up (by port)."""
    port = _jupyter_port()
    running = _port_in_use(port)
    return JupyterStatusResponse(
        status="running" if running else "stopped",
        port=port if running else None,
    )
