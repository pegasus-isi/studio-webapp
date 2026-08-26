"""Settings endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from models import LLMConfigRequest, SettingsResponse

log = structlog.get_logger()

router = APIRouter(prefix="/api/settings", tags=["settings"])


class RegistryRequest(BaseModel):
    """Container registry configuration for the image Build action."""

    registry: str = ""
    username: str = ""
    # Omitted/None keeps the stored password; empty string clears it.
    password: str | None = None


@router.get("/registry")
async def get_registry() -> dict:
    """Registry config for the settings UI. The password is never echoed."""
    from main import db

    return {
        "registry": await db.get_setting("container_registry", "") or "",
        "username": await db.get_setting("container_registry_username", "") or "",
        "has_password": bool(
            await db.get_setting("container_registry_password", "")
        ),
    }


@router.put("/registry")
async def update_registry(req: RegistryRequest) -> dict:
    """Persist registry config. The password is encrypted at rest (S7)."""
    import secretbox
    from main import db

    await db.set_setting("container_registry", req.registry.strip())
    await db.set_setting("container_registry_username", req.username.strip())
    if req.password is not None:
        await db.set_setting(
            "container_registry_password", secretbox.encrypt(req.password)
        )
    return await get_registry()


class UIPreferences(BaseModel):
    """Per-user UI preferences for the non-specialist redesign (#241).

    Server-side rather than localStorage because these must follow the *user*,
    not the browser. Someone who tries Studio from a laptop and then a lab
    machine should not silently get two different products, and the rollout
    flag has to mean the same thing in both places for a staged rollout to be
    interpretable at all.
    """

    # Phase 1 rollout flag, now on by default (#241): the unified Run flow is
    # the product, and the four Pegasus verbs are the opt-out.
    #
    # This was flipped ahead of the terminology check and the Phase 8 task
    # test, which the plan named as the gate. The vocabulary the flow uses is
    # therefore still unvalidated with real users, and the honest reading of a
    # complaint about wording is "we haven't tested this yet", not "users
    # disagree with the research". Turning it off per user in Settings remains
    # the escape hatch, and remains the first thing to suggest.
    simple_run: bool = True
    # Whether the Advanced disclosure (the individual Generate/Build/Plan/Run
    # verbs) starts open. Persisted so a Pegasus-literate user doesn't reopen
    # it on every visit.
    advanced_open: bool = False


_UI_PREFS_KEY = "ui_preferences"


@router.get("/ui", response_model=UIPreferences)
async def get_ui_preferences() -> UIPreferences:
    """UI preferences, with defaults for any key never set."""
    from main import db

    stored = await db.get_setting(_UI_PREFS_KEY, {}) or {}
    if not isinstance(stored, dict):
        stored = {}
    return UIPreferences(**{
        k: v for k, v in stored.items() if k in UIPreferences.model_fields
    })


@router.put("/ui", response_model=UIPreferences)
async def update_ui_preferences(req: UIPreferences) -> UIPreferences:
    """Replace UI preferences wholesale (the payload is small and complete)."""
    from main import db

    await db.set_setting(_UI_PREFS_KEY, req.model_dump())
    log.info("ui_preferences_updated", **req.model_dump())
    return req


@router.get("", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    from main import db

    llm_data = await db.get_llm_config()
    llm_resp = None
    if llm_data:
        # Write-only key treatment (SEC-3), same as GET /api/llm/config.
        from routers.llm import _config_response

        llm_resp = _config_response(llm_data)

    installed = await db.list_tools()
    tool_ids = [t["tool_id"] for t in installed]

    return SettingsResponse(llm=llm_resp, installed_tools=tool_ids)


@router.put("", response_model=SettingsResponse)
async def update_settings(req: LLMConfigRequest) -> SettingsResponse:
    """Legacy LLM-config save. Routes through the same guarded path as
    ``PUT /api/llm/config`` (BE-5) so it can't wipe the stored key on an
    omitted-key save or persist a keyless required-key provider."""
    from main import db
    from routers.llm import save_llm_config

    await save_llm_config(db, req)
    return await get_settings()
