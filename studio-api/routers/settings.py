"""Settings endpoints."""

from __future__ import annotations

import os
from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

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


class PlanModeRequest(BaseModel):
    """Where `pegasus-plan` should run.

    ``auto`` detects it (remote when the schedd is on another host), and is
    what ships. The other two are overrides, and the reason they exist is that
    the interesting question — does planning in this workspace produce a DAG
    the submit host can execute — cannot be answered by reading anything. You
    have to try it. Until now that meant editing STUDIO_PLAN_ON_SUBMIT_HOST in
    the spawner profile and restarting the pod, which is not a thing a user can
    do to their own session.
    """

    mode: str = "auto"


async def _plan_mode_response() -> dict:
    """The stored choice plus what the next run would actually do."""
    from services import run_flow, submit_host

    stored = await run_flow._plan_mode_setting()
    effective, source = await run_flow._resolve_plan_mode()
    return {
        "mode": stored,
        "choices": list(run_flow.PLAN_MODE_CHOICES),
        # What the next run does, and why — with `auto` the answer is a
        # detection result, and showing it is the difference between a switch
        # you can reason about and one you have to experiment with.
        "effective": effective,
        "source": source,
        # None when undetermined; the UI must not turn that into "co-located".
        "submit_host_is_remote": await submit_host.is_remote(),
        "env_default": os.environ.get(
            "STUDIO_PLAN_ON_SUBMIT_HOST", "auto"
        ).strip().lower(),
    }


@router.get("/planning")
async def get_planning() -> dict:
    """Planning location: the stored preference and the effective outcome."""
    return await _plan_mode_response()


@router.put("/planning")
async def update_planning(req: PlanModeRequest) -> dict:
    """Persist where planning runs.

    Deliberately accepts "local" even when the submit host is remote and the
    two Pegasus installs disagree. Refusing it would remove the only way to
    check whether that is still true, and the run flow already catches the
    mismatch in its preflight — where it can name this setting as the cause
    and point at the switch that undoes it, rather than failing 25 nodes deep.
    """
    from main import db
    from services import run_flow

    mode = (req.mode or "").strip().lower()
    if mode not in run_flow.PLAN_MODE_CHOICES:
        raise HTTPException(
            status_code=400,
            detail=(
                "mode must be one of "
                f"{', '.join(run_flow.PLAN_MODE_CHOICES)}: {req.mode!r}"
            ),
        )

    await db.set_setting("plan_mode", mode)
    log.info("plan_mode_updated", mode=mode)
    return await _plan_mode_response()
class WorkflowSecretRequest(BaseModel):
    """One credential a workflow's generator reads from its environment."""

    name: str
    value: str


async def _workflow_secrets_response() -> dict:
    """Names and timestamps. Values are never returned, by design.

    Write-only, like the LLM keys (SEC-3): a GET that echoed them would put
    every credential in the workspace one request — or one devtools tab — away,
    and nothing in the UI needs the value back.
    """
    from main import db
    import secretbox

    return {
        "secrets": await db.list_workflow_secrets(),
        # Whether the values are actually encrypted on disk. secretbox is
        # fail-open by design, so the UI has to be able to say which.
        "encrypted_at_rest": secretbox.is_active(),
    }


@router.get("/workflow-secrets")
async def get_workflow_secrets() -> dict:
    """Which credentials are set, and when they were last changed."""
    return await _workflow_secrets_response()


@router.put("/workflow-secrets")
async def update_workflow_secret(req: WorkflowSecretRequest) -> dict:
    """Store or replace one credential."""
    from main import db
    from services import workflow_secrets

    try:
        name = workflow_secrets.normalize_name(req.name)
        value = workflow_secrets.validate_value(req.value)
    except workflow_secrets.InvalidSecret as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    await db.set_workflow_secret(name, value)
    # Name only. A log line carrying the value would outlive every other
    # protection this feature has.
    log.info("workflow_secret_set", name=name)
    return await _workflow_secrets_response()


@router.delete("/workflow-secrets/{name}")
async def delete_workflow_secret(name: str) -> dict:
    """Remove one credential."""
    from main import db
    from services import workflow_secrets

    try:
        canonical = workflow_secrets.normalize_name(name)
    except workflow_secrets.InvalidSecret as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not await db.delete_workflow_secret(canonical):
        raise HTTPException(status_code=404, detail=f"No secret named {canonical}")
    log.info("workflow_secret_deleted", name=canonical)
    return await _workflow_secrets_response()


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
    # Skip the landing page at `/` and open Home directly. Distinct from
    # `welcome_dismissed`, which records that the first-run tour has been
    # shown: the landing page is a marketing-style introduction that a
    # returning user may never want to see again, and the Next server reads
    # this flag when deciding where the app root redirects.
    skip_welcome: bool = False
    # Which agent engine the chat panel talks to (#367).
    #
    # "opencode" makes the panel a client of the supervised `opencode serve`
    # process, which brings context compaction, cross-session memory,
    # subagents, MCP and skills — none of which the builtin loop has. It is
    # opt-in while it is being tried in a real pod: the builtin loop is what
    # every existing conversation was written against, and a per-user
    # preference means switching back is a toggle rather than a rollback.
    #
    # A string rather than a boolean because the intended end state is one
    # engine, not two (the builtin loop is to be deleted once this is proven),
    # and a retired value should read as a stale name, not an inverted flag.
    chat_engine: Literal["builtin", "opencode"] = "builtin"


_UI_PREFS_KEY = "ui_preferences"


@router.get("/ui", response_model=UIPreferences)
async def get_ui_preferences() -> UIPreferences:
    """UI preferences, with defaults for any key never set."""
    from main import db

    stored = await db.get_setting(_UI_PREFS_KEY, {}) or {}
    if not isinstance(stored, dict):
        stored = {}
    known = {k: v for k, v in stored.items() if k in UIPreferences.model_fields}
    try:
        return UIPreferences(**known)
    except ValidationError:
        # A stored *value* that no longer validates — a retired `chat_engine`
        # name, a string where a bool belongs — must not 500 this endpoint and
        # take every other preference down with it. Keep the fields that stand
        # on their own and default the rest.
        safe: dict[str, object] = {}
        for key, value in known.items():
            try:
                UIPreferences(**{key: value})
            except ValidationError:
                log.warning("ui_preference_invalid", key=key, value=value)
                continue
            safe[key] = value
        return UIPreferences(**safe)


@router.put("/ui", response_model=UIPreferences)
async def update_ui_preferences(req: UIPreferences) -> UIPreferences:
    """Replace UI preferences wholesale (the payload is small and complete)."""
    from main import db

    await db.set_setting(_UI_PREFS_KEY, req.model_dump())
    log.info("ui_preferences_updated", **req.model_dump())
    return req


class TelemetryConsent(BaseModel):
    """Whether prompts and replies may be exported with this user's traces."""

    content_capture: bool = False
    # Read-only for the client: whether this deployment has a collector at all.
    # Without one nothing is exported whatever the flag says, and a control
    # that implies otherwise would be a lie about where the data goes.
    collector_configured: bool = False


@router.get("/telemetry", response_model=TelemetryConsent)
async def get_telemetry_consent() -> TelemetryConsent:
    from main import db
    from services import telemetry

    return TelemetryConsent(
        content_capture=await telemetry.content_capture(db),
        collector_configured=telemetry.collector_configured(),
    )


@router.put("/telemetry", response_model=TelemetryConsent)
async def update_telemetry_consent(req: TelemetryConsent) -> TelemetryConsent:
    """Store the consent flag and push it to the engine.

    Propagated, not just stored, for the same reason the shell-approval setting
    is: the engine reads its plugin options out of the generated opencode.json
    at startup and no endpoint reloads it, so without this the Settings page
    would claim content capture was off while the running engine kept exporting
    prompts for the rest of the pod's life. `propagate` rewrites the config and
    schedules the restart itself (the restart is not awaited — supervisorctl
    blocks for a stop *and* a start).
    """
    from main import db
    from services import telemetry

    await telemetry.set_content_capture(db, req.content_capture)
    try:
        from llm.propagator import LLMPropagator

        await LLMPropagator().propagate(db)
    except Exception as e:
        # Stored but not propagated: the flag is right in the DB and the engine
        # is wrong until its next restart. Surfaced as a warning rather than a
        # 500 because failing the request would suggest the choice was not
        # recorded, and re-toggling would then be the natural thing to try.
        log.warning("telemetry_consent_propagate_failed", error=str(e))

    return TelemetryConsent(
        content_capture=req.content_capture,
        collector_configured=telemetry.collector_configured(),
    )


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
