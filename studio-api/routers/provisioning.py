"""Provisioning endpoints — read-only pool inventory and supply/demand.

V1 of the provisioning surface (deck slide 11): show what the pool has, what
the queue wants, and any pressure findings. Provisioning *actions* (Annex/
FABRIC/JetStream adapters) come later in Phase 3.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter

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
