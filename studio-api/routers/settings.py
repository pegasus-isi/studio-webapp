"""Settings endpoints."""

from __future__ import annotations

import os

import structlog
from fastapi import APIRouter, HTTPException
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


class WorkflowScanRequest(BaseModel):
    """User additions to workflow run discovery (issue #306).

    Both lists *extend* the deployment defaults (STUDIO_SCAN_DIR and the
    builtin exclusion fragments) — they never replace them, so a user can't
    configure themselves into an empty Workflows page.
    """

    # Extra directories to scan for runs (absolute paths).
    roots: list[str] = []
    # Extra path fragments to exclude — plain substrings, matched against the
    # run directory path exactly like the builtin list.
    excludes: list[str] = []


async def _workflow_scan_response() -> dict:
    from main import db
    from services.backends import active_backend
    from services.workflow_scanner import _EXCLUDE_RUN_PATH_FRAGMENTS

    backend = active_backend()

    roots = await db.get_setting("workflow_scan_roots", []) or []
    excludes = await db.get_setting("workflow_scan_excludes", []) or []
    return {
        "roots": roots,
        "excludes": excludes,
        # Roots that don't exist right now (unmounted volume, typo). The scan
        # skips them silently, so the UI is where the user learns about it.
        "missing_roots": [r for r in roots if not os.path.isdir(r)],
        "defaults": {
            "scan_root": backend.scan_root(),
            "workflows_dir": backend.workflows_dir(),
            "builtin_excludes": list(_EXCLUDE_RUN_PATH_FRAGMENTS),
        },
    }


@router.get("/workflow-scan")
async def get_workflow_scan() -> dict:
    """Current run-discovery config: user additions plus the env defaults."""
    return await _workflow_scan_response()


@router.put("/workflow-scan")
async def update_workflow_scan(req: WorkflowScanRequest) -> dict:
    """Persist extra scan roots / exclude fragments.

    Absolute paths only for roots — a relative path would silently resolve
    against whatever cwd the server happens to have. Nonexistent roots are
    accepted (a volume may mount later) but reported via ``missing_roots``.
    """
    from main import db

    roots: list[str] = []
    for raw in req.roots:
        root = raw.strip()
        if not root:
            continue
        if not os.path.isabs(root):
            raise HTTPException(
                status_code=400,
                detail=f"Scan roots must be absolute paths: {root!r}",
            )
        if root not in roots:
            roots.append(root)

    excludes: list[str] = []
    for raw in req.excludes:
        frag = raw.strip()
        if frag and frag not in excludes:
            excludes.append(frag)

    await db.set_setting("workflow_scan_roots", roots)
    await db.set_setting("workflow_scan_excludes", excludes)
    log.info("workflow_scan_config_updated", roots=roots, excludes=excludes)
    return await _workflow_scan_response()


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
    # First-run welcome (#241, Phase 6). Persisted server-side, not in
    # localStorage: "have I introduced myself to this person" is a fact about
    # the user, and clearing a browser or opening Studio on a second machine
    # should not restart the introduction.
    welcome_dismissed: bool = False
    # Whether the sidebar's Advanced group (Resources, Terminal) starts open
    # (#241, Phase 7). Separate from `advanced_open`, which is the run form's
    # disclosure — someone who wants the individual Pegasus verbs does not
    # necessarily want a terminal in their sidebar, and coupling two unrelated
    # disclosures to one flag makes both surprising.
    nav_advanced_open: bool = False


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
