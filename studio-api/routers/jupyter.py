"""JupyterLab status endpoint.

JupyterLab runs as an always-on service under supervisord
(docker/supervisor/scripts/jupyter.sh), started with the container. The studio
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

DEFAULT_JUPYTER_PORT = 8889


def jupyter_port() -> int:
    """The port JupyterLab was started on for this session.

    Read per call, not frozen at import: the launcher can allocate a free port
    per session (STUDIO_ALLOC_PORTS), and a module constant captured at import
    time is how the same value ends up wrong in one process and right in
    another. Same reason `main.py` reads STUDIO_SCAN_DIR at startup rather than
    importing workflow_scanner's frozen SCAN_ROOT.
    """
    raw = (os.environ.get("STUDIO_JUPYTER_PORT") or "").strip()
    if not raw:
        return DEFAULT_JUPYTER_PORT
    try:
        return int(raw)
    except ValueError:
        log.warning(
            "jupyter_port_invalid", value=raw, fallback=DEFAULT_JUPYTER_PORT
        )
        return DEFAULT_JUPYTER_PORT


def _port_in_use(port: int | None = None) -> bool:
    """True if the JupyterLab server is listening on its port."""
    if port is None:
        port = jupyter_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


@router.get("/status", response_model=JupyterStatusResponse)
async def jupyter_status() -> JupyterStatusResponse:
    """Report whether the JupyterLab service is up (by port)."""
    port = jupyter_port()
    running = _port_in_use(port)
    return JupyterStatusResponse(
        status="running" if running else "stopped",
        port=port if running else None,
    )
