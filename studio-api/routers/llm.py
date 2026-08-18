"""LLM configuration endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException

from llm.propagator import LLMPropagator
from llm.providers import (
    PROVIDER_DEFAULTS,
    PROVIDERS,
    check_api_key_requirement,
    fetch_models,
    provider_type,
)
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


def _config_response(data: dict) -> LLMConfigResponse:
    """Serialize a stored LLM config with the key as presence-only.

    Keys are write-only (SEC-3): any script running in the studio origin can
    read GET responses, so the raw key must never leave the server.
    """
    return LLMConfigResponse(
        **{k: v for k, v in data.items() if k != "api_key"},
        has_api_key=bool(data.get("api_key")),
    )


def _provider_response(row: dict) -> ProviderConfigResponse:
    """ProviderConfigResponse with the same write-only key treatment."""
    return ProviderConfigResponse(
        **{k: v for k, v in row.items() if k != "api_key"},
        has_api_key=bool(row.get("api_key")),
    )


async def _propagate_and_restart(db) -> None:
    """Propagate the active LLM config to installed tools, then restart the
    OpenCode chat server so it actually uses the new provider.

    ``opencode serve`` reads provider config once at startup; a running instance
    keeps the old provider — so switching providers (or dropping one whose
    streaming is broken) silently has no effect on chat until serve restarts.
    Stopping it here makes the next chat turn re-spawn with the new config.
    """
    try:
        await LLMPropagator().propagate(db)
    except Exception as e:
        log.warning("llm_propagation_failed", error=str(e))
    try:
        from services.opencode_engine import engine as opencode_engine

        # Existing opencode sessions are bound to the old provider/model and
        # would fail with "Model not found" after a switch — drop the mapping so
        # the next turn creates fresh sessions on the new provider.
        await opencode_engine.forget_sessions(db)
        await opencode_engine.restart()
        log.info("opencode_restarted_on_llm_change")
    except Exception as e:
        log.warning("opencode_restart_failed", error=str(e))


@router.get("/config", response_model=LLMConfigResponse)
async def get_llm_config() -> LLMConfigResponse:
    from main import db

    data = await db.get_llm_config()
    if data is None:
        # get_llm_config() bakes in the gateway default, so this is defensive.
        return LLMConfigResponse(
            provider="gateway",
            model=PROVIDER_DEFAULTS["gateway"]["model"],
            base_url=PROVIDER_DEFAULTS["gateway"]["base_url"],
        )
    return _config_response(data)


async def save_llm_config(db, req: LLMConfigRequest) -> None:
    """Validate + persist an LLM config, then propagate to tools.

    Shared by ``PUT /api/llm/config`` and the legacy ``PUT /api/settings`` so
    both enforce the same guards (BE-5): a request that omits the key keeps the
    stored one (never wipes it), and a keyed provider saved without a key is
    rejected up front instead of failing deep in chat as "No api key passed
    in". Raises ``HTTPException(400)`` on an invalid config.
    """
    api_key = req.api_key
    base_url = req.base_url
    if not api_key:
        existing = await db.get_llm_config()
        if existing and existing.get("provider") == req.provider.value:
            api_key = existing.get("api_key") or None
            base_url = base_url or existing.get("base_url") or None

    error = await check_api_key_requirement(
        req.provider.value, api_key or "", base_url or ""
    )
    if error:
        raise HTTPException(status_code=400, detail=error)

    await db.set_llm_config(
        provider=req.provider.value,
        model=req.model,
        api_key=api_key,
        base_url=base_url,
        extra_config=req.extra_config,
    )
    log.info("llm_config_updated", provider=req.provider.value, model=req.model)

    await _propagate_and_restart(db)


@router.put("/config", response_model=LLMConfigResponse)
async def update_llm_config(req: LLMConfigRequest) -> LLMConfigResponse:
    from main import db

    await save_llm_config(db, req)
    data = await db.get_llm_config()
    return _config_response(data or {})


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


async def _list_models(
    provider: str, api_key: str, base_url: str | None
) -> ValidateResponse:
    """Fetch the models a provider offers (shared by /validate and the
    saved-provider models endpoint)."""
    # Normalize custom-<id> (multiple custom endpoints) to the "custom" preset.
    ptype = provider_type(provider)
    preset = PROVIDERS.get(ptype)
    if not preset:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    # Resolve base URL
    defaults = PROVIDER_DEFAULTS.get(ptype, {})
    resolved_url = base_url or defaults.get("base_url", "")

    if not resolved_url:
        return ValidateResponse(
            valid=False, error="No base URL configured for this provider."
        )

    # Anthropic doesn't have a standard /models endpoint
    if ptype == "anthropic":
        # Test with a simple API call
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m["id"] for m in data.get("data", [])]
                    return ValidateResponse(valid=True, models=models)
                # Report the status (e.g. 401 for a bad key) without echoing the
                # raw upstream body back to the client.
                return ValidateResponse(
                    valid=False,
                    error=f"HTTP {resp.status_code} {resp.reason_phrase}".strip(),
                )
        except Exception as e:
            return ValidateResponse(valid=False, error=str(e))

    # OpenAI-compatible: fetch /models
    models, error = await fetch_models(resolved_url, api_key)
    if error:
        return ValidateResponse(valid=False, error=error)
    return ValidateResponse(valid=True, models=models)


@router.post("/validate", response_model=ValidateResponse)
async def validate_provider(req: ValidateRequest) -> ValidateResponse:
    """Test API key connectivity by fetching models from the provider."""
    return await _list_models(req.provider, req.api_key, req.base_url)


# --- Saved provider configs ---


@router.get("/provider-configs")
async def list_provider_configs() -> dict[str, list[ProviderConfigResponse]]:
    """List all saved provider configurations."""
    from main import db

    rows = await db.list_provider_configs()
    return {
        "configs": [_provider_response(r) for r in rows],
    }


@router.get(
    "/provider-configs/{provider_id}/models", response_model=ValidateResponse
)
async def provider_config_models(provider_id: str) -> ValidateResponse:
    """List the models a saved provider offers, using its STORED key.

    Companion to keys being write-only (SEC-3): the browser can no longer
    read a saved key to call /validate itself, so the lookup runs server-side.
    An id with no saved row falls back to preset defaults with no key —
    matching what the old client-side call could do for keyless providers
    (gateway, ollama)."""
    from main import db

    row = await db.get_provider_config(provider_id) or {}
    return await _list_models(
        provider_id,
        row.get("api_key") or "",
        row.get("base_url") or None,
    )


@router.put("/provider-configs")
async def upsert_provider_config(req: ProviderConfigRequest) -> ProviderConfigResponse:
    """Create or update a saved provider configuration."""
    from main import db

    # Same no-wipe rule as PUT /config: an omitted key keeps the saved one.
    api_key = req.api_key
    if not api_key:
        existing = await db.get_provider_config(req.provider_id)
        if existing:
            api_key = existing.get("api_key") or ""

    # A custom endpoint is created empty and configured afterwards, in the card
    # that only renders once the row exists. Validating the create therefore
    # deadlocked it: the field that supplies the base URL could not appear until
    # a row was saved, and the row could not be saved without the base URL, so
    # custom providers could not be added at all.
    #
    # The draft is allowed through instead. Activation still validates (see
    # activate_provider below), so an unconfigured endpoint can be saved but
    # never used.
    draft = provider_type(req.provider_id) == "custom" and not (
        req.base_url or ""
    ).strip()

    if not draft:
        error = await check_api_key_requirement(
            req.provider_id, api_key, req.base_url
        )
        if error:
            raise HTTPException(status_code=400, detail=error)

    await db.upsert_provider_config(
        provider_id=req.provider_id,
        name=req.name,
        api_key=api_key,
        base_url=req.base_url,
        default_model=req.default_model,
        is_active=req.is_active and not draft,
    )

    # If this is the active provider, sync to llm_config. Never for a draft:
    # the first provider added is submitted with is_active, and activating one
    # with no base URL would leave chat pointing at an endpoint that cannot work.
    if req.is_active and not draft:
        await db.set_active_provider(req.provider_id)
    elif draft:
        log.info("provider_draft_saved", provider_id=req.provider_id)

    # Always propagate so tool configs (e.g. codex config.toml) stay in sync
    await _propagate_and_restart(db)

    row = await db.get_provider_config(req.provider_id)
    return _provider_response(row or {})


@router.post("/provider-configs/{provider_id}/activate")
async def activate_provider(provider_id: str) -> ProviderConfigResponse:
    """Set a provider as the active one and propagate config to all tools."""
    from main import db

    config = await db.get_provider_config(provider_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Provider not found: {provider_id}")

    # Guard legacy rows saved before key validation existed.
    error = await check_api_key_requirement(
        provider_id, config.get("api_key") or "", config.get("base_url") or ""
    )
    if error:
        raise HTTPException(status_code=400, detail=error)

    await db.set_active_provider(provider_id)

    await _propagate_and_restart(db)

    row = await db.get_provider_config(provider_id)
    return _provider_response(row or {})


@router.delete("/provider-configs/{provider_id}")
async def delete_provider_config(provider_id: str) -> dict[str, str]:
    """Delete a saved provider configuration.

    If it was the *active* provider, re-point the active LLM config at another
    saved provider (or the built-in gateway default) — otherwise llm_config
    dangles on the deleted endpoint and chat keeps using it.
    """
    from main import db

    active = await db.get_llm_config()
    was_active = bool(active and active.get("provider") == provider_id)

    await db.delete_provider_config(provider_id)

    if was_active:
        remaining = await db.list_provider_configs()
        if remaining:
            await db.set_active_provider(remaining[0]["provider_id"])
        else:
            g = PROVIDER_DEFAULTS["gateway"]
            await db.set_llm_config(
                provider="gateway",
                model=g["model"],
                api_key=None,
                base_url=g["base_url"],
            )
        log.info("llm_active_provider_deleted_fallback", deleted=provider_id)

    await _propagate_and_restart(db)
    return {"status": "deleted", "provider_id": provider_id}
