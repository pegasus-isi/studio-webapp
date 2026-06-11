"""Settings endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter

from models import LLMConfigRequest, LLMConfigResponse, SettingsResponse

log = structlog.get_logger()

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    from main import db

    llm_data = await db.get_llm_config()
    llm_resp = None
    if llm_data:
        llm_resp = LLMConfigResponse(**llm_data)

    installed = await db.list_tools()
    tool_ids = [t["tool_id"] for t in installed]

    return SettingsResponse(llm=llm_resp, installed_tools=tool_ids)


@router.put("", response_model=SettingsResponse)
async def update_settings(req: LLMConfigRequest) -> SettingsResponse:
    from main import db

    await db.set_llm_config(
        provider=req.provider.value,
        model=req.model,
        api_key=req.api_key,
        base_url=req.base_url,
        extra_config=req.extra_config,
    )

    # Propagate to installed tools
    try:
        from llm.propagator import LLMPropagator

        propagator = LLMPropagator()
        await propagator.propagate(db)
    except Exception as e:
        log.warning("llm_propagation_failed", error=str(e))

    return await get_settings()
