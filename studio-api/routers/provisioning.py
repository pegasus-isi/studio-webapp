"""Provisioning endpoints: pool inventory, supply/demand, and the annex.

Shows what the pool has, what the queue wants, and any pressure findings. On
the hosted deployment it also drives the user's own annex (Bring Your Own
Compute): download its setup bundle, close the request, shut it down.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from services import annex, condor_pool, site_config

log = structlog.get_logger()

router = APIRouter(prefix="/api/provisioning", tags=["provisioning"])


@router.get("/status")
async def provisioning_status(force: bool = False) -> dict:
    """Pool inventory + queue demand + pressure findings (cached snapshot),
    plus which deployment this is and, on the hosted one, the user's annex.

    The pool snapshot is shared with the dashboard and the notification
    engine, so the annex rides beside it rather than inside it: its queries
    hit the schedd and a second collector, and only this page wants them
    (#532). Not asked for at all under Open OnDemand, where the cluster is
    the install and there is no annex to have.
    """
    status = dict(await condor_pool.get_status(force=force))
    deployment = site_config.deployment()
    status["deployment"] = deployment
    if deployment == "hosted":
        status["annex"] = await annex.get_status(force=force)
    return status


def _hosted_only() -> None:
    # Under Open OnDemand the cluster is the install; there is no annex.
    if site_config.deployment() != "hosted":
        raise HTTPException(status_code=404, detail="No annex on this deployment")


@router.post("/annex/bundle")
async def create_annex_bundle() -> dict:
    """Create the setup bundle (`htcondor annex create <user>`), the Studio
    side of step 1 (#534). The tarball stays under ~/.studio/annex for the
    download below; the user carries it to the cluster themselves."""
    _hosted_only()
    result = await annex.create_bundle()
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "annex create failed"))
    return result


@router.get("/annex/bundle")
async def download_annex_bundle() -> StreamingResponse:
    """The current bundle as a download. It holds a token good for 90 days,
    hence attachment only, and nothing lists it.

    Opened here, before streaming, rather than handed to FileResponse by
    path: a create that lands meanwhile deletes the old directory, and an
    open file survives that while a path does not.
    """
    _hosted_only()
    bundle = annex.current_bundle()
    if not bundle:
        raise HTTPException(status_code=404, detail="No annex bundle yet")
    try:
        fh = open(bundle["path"], "rb")  # noqa: SIM115 - handed to the stream
    except OSError:
        raise HTTPException(status_code=404, detail="No annex bundle yet") from None

    def chunks():
        with fh:
            while True:
                block = fh.read(64 * 1024)
                if not block:
                    return
                yield block

    return StreamingResponse(
        chunks(),
        media_type="application/x-tar",
        headers={
            "Content-Disposition": f'attachment; filename="{bundle["filename"]}"',
            "Content-Length": str(bundle["size"]),
        },
    )


@router.post("/annex/close")
async def close_annex_requests() -> dict:
    """Remove the annex's leftover request jobs (#558): retired or held
    requests that no node is left to end."""
    _hosted_only()
    result = await annex.close_requests()
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "closing the requests failed"))
    return result


@router.post("/annex/shutdown")
async def shutdown_annex() -> dict:
    """`htcondor annex shutdown <user>`: release the nodes now."""
    _hosted_only()
    result = await annex.shutdown()
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "annex shutdown failed"))
    return result

