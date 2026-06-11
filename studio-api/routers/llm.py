"""LLM configuration endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException

from llm.propagator import LLMPropagator
from llm.providers import PROVIDER_DEFAULTS, PROVIDERS, fetch_models
from models import (
    LLMConfigRequest,
    LLMConfigResponse,
    ProviderConfigRequest,
    ProviderConfigResponse,
    ProviderInfo,
    ValidateRequest,
    ValidateResponse,
)

log = structlog.get_logger()

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.get("/config", response_model=LLMConfigResponse)
async def get_llm_config() -> LLMConfigResponse:
    from main import db

    data = await db.get_llm_config()
    if data is None:
        return LLMConfigResponse(provider="anthropic")
    return LLMConfigResponse(**data)


@router.put("/config", response_model=LLMConfigResponse)
async def update_llm_config(req: LLMConfigRequest) -> LLMConfigResponse:
    from main import db

    await db.set_llm_config(
        provider=req.provider.value,
        model=req.model,
        api_key=req.api_key,
        base_url=req.base_url,
        extra_config=req.extra_config,
    )
    log.info("llm_config_updated", provider=req.provider.value, model=req.model)

    # Propagate to installed tools
    try:
        propagator = LLMPropagator()
        await propagator.propagate(db)
    except Exception as e:
        log.warning("llm_propagation_failed", error=str(e))

    data = await db.get_llm_config()
    return LLMConfigResponse(**(data or {}))


@router.get("/providers")
async def list_providers() -> dict[str, list[ProviderInfo]]:
    providers = []
    for pid, preset in PROVIDERS.items():
        defaults = PROVIDER_DEFAULTS.get(pid, {})
        providers.append(ProviderInfo(
            id=pid,
            name=preset["name"],
            base_url=defaults.get("base_url"),
            default_model=preset.get("default_model", ""),
            api_key_env=preset.get("api_key_env"),
        ))
    return {"providers": providers}


@router.post("/validate", response_model=ValidateResponse)
async def validate_provider(req: ValidateRequest) -> ValidateResponse:
    """Test API key connectivity by fetching models from the provider."""
    provider = req.provider.value
    preset = PROVIDERS.get(provider)
    if not preset:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    # Resolve base URL
    defaults = PROVIDER_DEFAULTS.get(provider, {})
    base_url = req.base_url or defaults.get("base_url", "")

    if not base_url:
        return ValidateResponse(
            valid=False, error="No base URL configured for this provider."
        )

    # Anthropic doesn't have a standard /models endpoint
    if provider == "anthropic":
        # Test with a simple API call
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": req.api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m["id"] for m in data.get("data", [])]
                    return ValidateResponse(valid=True, models=models)
                return ValidateResponse(
                    valid=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                )
        except Exception as e:
            return ValidateResponse(valid=False, error=str(e))

    # OpenAI-compatible: fetch /models
    models, error = await fetch_models(base_url, req.api_key)
    if error:
        return ValidateResponse(valid=False, error=error)
    return ValidateResponse(valid=True, models=models)


# --- Saved provider configs ---


@router.get("/provider-configs")
async def list_provider_configs() -> dict[str, list[ProviderConfigResponse]]:
    """List all saved provider configurations."""
    from main import db

    rows = await db.list_provider_configs()
    return {
        "configs": [ProviderConfigResponse(**r) for r in rows],
    }


@router.put("/provider-configs")
async def upsert_provider_config(req: ProviderConfigRequest) -> ProviderConfigResponse:
    """Create or update a saved provider configuration."""
    from main import db

    await db.upsert_provider_config(
        provider_id=req.provider_id,
        name=req.name,
        api_key=req.api_key,
        base_url=req.base_url,
        default_model=req.default_model,
        is_active=req.is_active,
    )

    # If this is the active provider, sync to llm_config
    if req.is_active:
        await db.set_active_provider(req.provider_id)

    # Always propagate so tool configs (e.g. codex config.toml) stay in sync
    try:
        propagator = LLMPropagator()
        await propagator.propagate(db)
    except Exception as e:
        log.warning("llm_propagation_failed", error=str(e))

    row = await db.get_provider_config(req.provider_id)
    return ProviderConfigResponse(**(row or {}))


@router.post("/provider-configs/{provider_id}/activate")
async def activate_provider(provider_id: str) -> ProviderConfigResponse:
    """Set a provider as the active one and propagate config to all tools."""
    from main import db

    config = await db.get_provider_config(provider_id)
    if not config:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Provider not found: {provider_id}")

    await db.set_active_provider(provider_id)

    try:
        propagator = LLMPropagator()
        await propagator.propagate(db)
    except Exception as e:
        log.warning("llm_propagation_failed", error=str(e))

    row = await db.get_provider_config(provider_id)
    return ProviderConfigResponse(**(row or {}))


@router.delete("/provider-configs/{provider_id}")
async def delete_provider_config(provider_id: str) -> dict[str, str]:
    """Delete a saved provider configuration."""
    from main import db

    await db.delete_provider_config(provider_id)
    return {"status": "deleted", "provider_id": provider_id}
