"""Same-origin reverse proxy for the PegasusAI Bot cell-toolbar client.

The ``@gclab/pegasus-ai-client`` JupyterLab extension calls its server from
the browser. The bot server (``PEGASUS_BOT_SERVER_URL``, default
``http://server.ai-bot:8000``) is a cluster-internal HTTP service — not
reachable from the browser: it would be blocked as mixed content on the HTTPS
studio origin, and its hostname only resolves inside the cluster. So the
extension is pointed at this same-origin endpoint, which forwards the request
to the configured bot server from inside the pod (Python resolves cluster DNS
at request time, so studio startup doesn't depend on the bot being up).

Only the extension's known endpoints are forwarded, and only to the single
configured upstream — this is not an open proxy.
"""

from __future__ import annotations

import os

import httpx
import structlog
from fastapi import APIRouter, Request, Response

log = structlog.get_logger()

router = APIRouter(prefix="/api/pegasus-bot", tags=["pegasus-bot"])

# Endpoints the extension calls: sendSearchRequest -> /search,
# sendFeedbackRequest -> /feedback.
_ALLOWED = {"search", "feedback"}
_TIMEOUT = 120.0


def _upstream() -> str:
    return os.environ.get(
        "PEGASUS_BOT_SERVER_URL", "http://server.ai-bot:8000"
    ).rstrip("/")


@router.post("/{path:path}")
async def proxy(path: str, request: Request) -> Response:
    """Forward a bot request to the configured upstream and relay the reply."""
    top = path.split("/", 1)[0]
    if top not in _ALLOWED:
        return Response(status_code=404)

    url = f"{_upstream()}/{path}"
    body = await request.body()
    content_type = request.headers.get("content-type", "application/json")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            upstream = await client.post(
                url, content=body, headers={"Content-Type": content_type}
            )
    except httpx.HTTPError as e:
        log.warning("pegasus_bot_proxy_failed", url=url, error=str(e))
        return Response(
            status_code=502,
            content='{"detail": "PegasusAI Bot server unreachable"}',
            media_type="application/json",
        )
    return Response(
        status_code=upstream.status_code,
        content=upstream.content,
        media_type=upstream.headers.get("content-type", "application/json"),
    )
