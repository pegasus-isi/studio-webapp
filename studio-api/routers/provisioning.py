"""Provisioning endpoints — read-only pool inventory and supply/demand.

V1 of the provisioning surface (deck slide 11): show what the pool has, what
the queue wants, and any pressure findings. Provisioning *actions* (Annex/
FABRIC/JetStream adapters) come later in Phase 3.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter

from services import condor_pool

log = structlog.get_logger()

router = APIRouter(prefix="/api/provisioning", tags=["provisioning"])


@router.get("/status")
async def provisioning_status(force: bool = False) -> dict:
    """Pool inventory + queue demand + pressure findings (cached snapshot)."""
    return await condor_pool.get_status(force=force)
