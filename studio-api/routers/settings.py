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


# --- Execution backend (compute target for workflow actions) ---


class ExecutionBackendRequest(BaseModel):
    """Selection for PUT /api/settings/execution-backend."""

    backend: str


@router.get("/execution-backend")
async def get_execution_backend() -> dict:
    """The active compute backend plus everything configured."""
    from services import backends as compute

    active = compute.active_backend()
    return {
        "backend": active.id,
        "backends": [
            {
                "id": b.id,
                "label": b.label,
                "plan_defaults": b.plan_defaults(),
                **b.status(),
            }
            for b in compute.all_backends()
        ],
    }


@router.put("/execution-backend")
async def put_execution_backend(req: ExecutionBackendRequest) -> dict:
    """Switch the compute backend for workflow actions.

    The chat-engine pattern: refuse to switch onto a target that fails its
    reachability probe, so a typo'd host can't silently break every
    workflow action."""
    from fastapi import HTTPException

    from main import db
    from services import backends as compute

    target = compute.get_backend(req.backend)
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown backend: {req.backend}"
        )
    err = await target.probe()
    if err:
        raise HTTPException(
            status_code=409,
            detail=f"Backend {req.backend} is unreachable: {err}",
        )

    previous = compute.active_backend()
    await db.set_setting("execution_backend", req.backend)
    compute.set_active_backend_id(req.backend)
    if previous.id != target.id:
        try:
            await previous.stop()
        except Exception as e:
            log.warning(
                "backend_stop_failed", backend=previous.id, error=str(e)
            )
        try:
            await target.start()
        except Exception as e:
            log.warning(
                "backend_start_failed", backend=target.id, error=str(e)
            )
    log.info("execution_backend_switched", backend=req.backend)
    return await get_execution_backend()


@router.put("", response_model=SettingsResponse)
async def update_settings(req: LLMConfigRequest) -> SettingsResponse:
    """Legacy LLM-config save. Routes through the same guarded path as
    ``PUT /api/llm/config`` (BE-5) so it can't wipe the stored key on an
    omitted-key save or persist a keyless required-key provider."""
    from main import db
    from routers.llm import save_llm_config

    await save_llm_config(db, req)
    return await get_settings()
