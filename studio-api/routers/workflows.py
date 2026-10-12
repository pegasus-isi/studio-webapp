"""Workflow monitoring and management endpoints."""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from models import (
    WorkflowListResponse,
    WorkflowProjectListResponse,
    WorkflowProjectResponse,
    WorkflowProjectRunResponse,
    WorkflowRunResponse,
)
from services.execution_sites import (
    apply_generator_site_defaults,
    hide_managed_params,
    execution_sites,
)
from services.generator_introspect import PLAN_PARAMS, introspect_generator
from services.procutil import communicate_or_kill
from services.workflow_monitor import (
    get_workflow_detail,
    get_workflow_jobs,
    run_pegasus_analyzer,
    tail_workflow_events,
)
from services.workflow_scanner import (
    discover_projects_async,
    discover_runs_async,
)
from services.workflow_submitter import (
    run_planned_workflow,
    submit_workflow,
)
from workspace import workspace_root


class GenerateRequest(BaseModel):
    """CLI args (already flag-formatted) to pass to workflow_generator.py."""

    args: list[str] = Field(default_factory=list)
    # Where the workflow will be planned. A gromacs-template generator binds
    # its transformations to this name, so it is passed as its -e (#466).
    site: str | None = None


class PlanRequest(BaseModel):
    """pegasus-plan options for Plan/Run."""

    site: str = "condorpool"
    output_site: str = "local"
    # Run action: the planned run dir (from a prior Plan) to submit via
    # pegasus-run. When omitted, the server locates the newest planned dir.
    run_dir: str | None = None


class BuildRequest(BaseModel):
    """Options for the container image Build action."""

    tag: str = "latest"
    # Build only by default. A workflow references its .sif by absolute path with
    # image_site="local" and Pegasus stages the file like any other input, so
    # pushing is a separate, opt-in step rather than part of building.
    push: bool = False


class RunFlowRequest(BaseModel):
    """Everything the one-button Run needs (#241, Phase 1).

    Combines what Generate and Plan/Run took separately, because the user makes
    these choices once, in one form, before pressing Run.
    """

    args: list[str] = Field(default_factory=list)
    site: str = "condorpool"
    output_site: str = "local"
    # Rebuild the software environment even when it looks current. The default
    # skips an up-to-date image, which is the slowest avoidable step in a
    # re-run; this is the escape hatch for when the definition changed in a way
    # mtime didn't catch.
    force_build: bool = False

log = structlog.get_logger()

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

WORKSPACE_ROOT = workspace_root()

_CMD_TIMEOUT = 300  # 5 minutes


def jupyter_path(abs_path: str) -> str:
    """`abs_path` relative to $HOME, which is JupyterLab's root.

    docker/supervisor/scripts/jupyter.sh roots the lab at $HOME, so a
    `/lab/tree/<x>` URL needs `x` relative to it. The browser only ever sees
    absolute container paths and cannot compute this, so it is served
    alongside them. Returns "" for anything outside $HOME rather than a
    `../..` path that would 404 in the lab.
    """
    home = os.path.realpath(os.environ.get("HOME", "/"))
    resolved = os.path.realpath(abs_path)
    if resolved == home:
        return ""
    if not resolved.startswith(home + os.sep):
        return ""
    return os.path.relpath(resolved, home)


@router.get("", response_model=WorkflowListResponse)
async def list_workflows() -> WorkflowListResponse:
    """List discovered workflow runs from the workspace."""
    from main import db

    # discover_runs scans SCAN_ROOT and excludes cloned-repo / test-fixture
    # braindumps, so only genuine runs are listed.
    runs = await discover_runs_async()
    workflows = []

    for run in runs:
        run_dir = run["run_dir"]

        # get_workflow_detail already resolves status (from events, or via
        # get_run_status in its own fallback), so we don't call get_run_status
        # separately here — that was up to two pegasus-status runs per run per
        # poll (BE-1 bonus).
        detail = await get_workflow_detail(run_dir)

        wf = WorkflowRunResponse(
            run_id=run["run_id"],
            run_name=run.get("run_name", ""),
            name=run["name"],
            run_dir=run_dir,
            status=detail.get("status", "unknown"),
            total_jobs=detail.get("total_jobs", 0),
            completed_jobs=detail.get("completed_jobs", 0),
            failed_jobs=detail.get("failed_jobs", 0),
            exec_site=detail.get("exec_site"),
            created_at=run.get("timestamp") or "",
            updated_at="",
        )
        workflows.append(wf)

        # Upsert into DB for persistence
        await db.upsert_workflow_run(
            run_id=run["run_id"],
            name=run["name"],
            run_dir=run_dir,
            status=detail.get("status", "unknown"),
            total_jobs=detail.get("total_jobs", 0),
            completed_jobs=detail.get("completed_jobs", 0),
            failed_jobs=detail.get("failed_jobs", 0),
            exec_site=detail.get("exec_site"),
        )

    # Onboarding funnel (#241). Also hooked here, not only on the dashboard: a
    # user who lands straight on Workflows and never opens the dashboard would
    # otherwise never have their submit/success milestones observed.
    try:
        from services import onboarding_metrics

        await onboarding_metrics.observe(
            db,
            has_runs=bool(workflows),
            has_success=any(w.status == "succeeded" for w in workflows),
        )
    except Exception as e:  # noqa: BLE001 — metrics must not break the list
        log.warning("onboarding_observe_failed", error=str(e))

    return WorkflowListResponse(workflows=workflows)


# ── Project endpoints (MUST be before /{run_id} to avoid path capture) ──


@router.get("/projects", response_model=WorkflowProjectListResponse)
async def list_projects() -> WorkflowProjectListResponse:
    """List workflow projects from ~/work/workflows/."""
    raw = await discover_projects_async()
    projects = [
        WorkflowProjectResponse(
            project_id=p["project_id"],
            name=p["name"],
            project_dir=p["project_dir"],
            status=p["status"],
            has_generator=p["has_generator"],
            has_workflow_yml=p["has_workflow_yml"],
            has_container_def=p["has_container_def"],
            jupyter_path=jupyter_path(p["project_dir"]),
            runs=[
                WorkflowProjectRunResponse(
                    **r, jupyter_path=jupyter_path(r["run_dir"])
                )
                for r in p["submitted_runs"]
            ],
        )
        for p in raw
    ]
    return WorkflowProjectListResponse(projects=projects)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str) -> dict:
    """Delete a workflow project and everything in it.

    Destructive and irreversible. Two guards:

    * the project must be one the scanner found, so an arbitrary
      ``project_id`` cannot name a directory to remove; and
    * its resolved path must still sit inside the workflows directory, which
      catches a symlinked project pointing somewhere else entirely.

    Runs inside it are cancelled and removed first — see
    ``workflow_submitter.delete_project`` for why that ordering matters.
    """
    from services.workflow_scanner import project_roots
    from services.workflow_submitter import delete_project as remove_project

    projects = await discover_projects_async()
    proj = next((p for p in projects if p["project_id"] == project_id), None)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    project_dir = os.path.realpath(proj["project_dir"])
    # Every directory the scanner lists from, not just the current one — a
    # project still in the legacy $HOME/work/workflows is listed, so it has to
    # be deletable too, or the button is there and returns 403.
    roots = [os.path.realpath(r) for r in project_roots()]
    if not any(project_dir.startswith(root + os.sep) for root in roots):
        # Belt and braces: the scanner only walks those dirs, but a symlinked
        # entry could still resolve outside them, and this deletes files.
        raise HTTPException(
            status_code=403, detail="Refusing to delete outside the workflows directory"
        )

    result = await remove_project(project_dir)
    if result.get("status") != "deleted":
        # 409, not 500: nothing is broken, the files are just busy right now.
        raise HTTPException(
            status_code=409,
            detail=result.get("output") or "Could not delete this workflow",
        )
    return {"project_id": project_id, **result}


class RenameProjectRequest(BaseModel):
    name: str


@router.post("/projects/{project_id}/rename")
async def rename_project(project_id: str, req: RenameProjectRequest) -> dict:
    """Rename a project's folder. See ``services/project_rename`` for the
    guards and what a move cannot fix."""
    from main import db
    from services import project_rename
    from services.run_flow import manager as run_flows
    from services.workflow_scanner import project_roots

    projects = await discover_projects_async()
    proj = next((p for p in projects if p["project_id"] == project_id), None)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    if run_flows.active(project_id):
        raise HTTPException(
            status_code=409,
            detail="A run is being prepared for this workflow. Wait for it, then rename.",
        )

    try:
        moved = project_rename.rename_project_dir(proj, req.name, project_roots())
    except project_rename.RenameError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None

    # The folder has moved; a failure from here on must not report the rename
    # as failed, or the user retries a move that already happened.
    records = 0
    for old in moved["old_paths"]:
        try:
            records += await db.move_run_dirs(old, moved["new_dir"])
        except Exception as e:  # noqa: BLE001
            log.warning("project_rename_records_failed", error=str(e))

    new_id = os.path.basename(moved["new_dir"])
    return {
        "status": "renamed",
        "project_id": new_id,
        "project_dir": moved["new_dir"],
        "catalogs_updated": moved["catalogs_updated"],
        "records_updated": records,
    }


@router.get("/projects/{project_id}/params")
async def get_project_params(project_id: str) -> dict:
    """Discover the configurable parameters for a project's actions.

    ``generator`` params are introspected from the workflow_generator.py
    argparse (different per workflow); ``plan`` params are the standard
    pegasus-plan options used by Plan/Submit.
    """
    projects = await discover_projects_async()
    proj = next((p for p in projects if p["project_id"] == project_id), None)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    generator: dict = {"params": [], "mutex_required": []}
    if proj["has_generator"]:
        generator = await introspect_generator(proj["project_dir"])
        generator = {
            **generator,
            # Studio sets the execution-site option itself, from "Where it
            # runs" (#466), so it is not offered as a second, conflicting field.
            "params": hide_managed_params(
                apply_generator_site_defaults(generator.get("params", []))
            ),
        }

    # The sites this project can actually plan against, so "Where it runs"
    # offers `compute` on a Slurm/hosted-catalog install instead of a
    # `condorpool` nothing defines there.
    sites = execution_sites(proj["project_dir"])
    plan_params = [
        {**p, "default": sites["default"]} if p["dest"] == "site" else p
        for p in PLAN_PARAMS
    ]

    return {
        "project_id": project_id,
        "generator": generator,
        "plan": {"params": plan_params},
        "sites": sites,
    }


@router.post("/projects/{project_id}/generate")
async def generate_workflow(
    project_id: str, req: GenerateRequest = GenerateRequest()
) -> dict:
    """Run workflow_generator.py in a project directory with the given args."""
    projects = await discover_projects_async()
    proj = next((p for p in projects if p["project_id"] == project_id), None)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    generator_file = proj.get("generator_file")
    if not proj["has_generator"] or not generator_file:
        raise HTTPException(
            status_code=400,
            detail="Project has no workflow generator script",
        )

    project_dir = proj["project_dir"]
    args = list(req.args)
    if req.site:
        from services.execution_sites import sync_site_arg
        from services.generator_introspect import introspect_generator

        params = (await introspect_generator(project_dir)).get("params") or []
        args = sync_site_arg(args, params, req.site)
    cmd = ["python3", generator_file, *args]

    # Same environment as the one-button Run flow gives the generator, so the
    # Advanced "Generate" verb is not the one path where a workflow's API keys
    # are missing (services/workflow_secrets.py).
    from services import workflow_secrets

    env = await workflow_secrets.subprocess_env()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
            env=env,
        )
        stdout, stderr = await communicate_or_kill(proc, _CMD_TIMEOUT)
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        output = out
        if err:
            output += f"\nstderr:\n{err}"

        if proc.returncode == 0:
            log.info("workflow_generated", project_id=project_id)
            return {"status": "generated", "output": output}
        else:
            return {"status": "error", "output": output}

    except FileNotFoundError:
        return {"status": "error", "output": "python3 not found"}
    except asyncio.TimeoutError:
        return {"status": "error", "output": f"{generator_file} timed out"}
    except Exception as e:
        return {"status": "error", "output": f"Error: {e}"}


@router.post("/projects/{project_id}/build")
async def build_project_image(
    project_id: str, req: BuildRequest = BuildRequest()
) -> StreamingResponse:
    """Build (and optionally push) the project's container, streaming the log.

    Builds the project's Apptainer definition into a .sif beside it, which is
    what the Transformation Catalog references. Registry + credentials come from
    settings (see /api/settings/registry); without a registry the .sif stays
    local, which is all a workflow needs.
    """
    from main import db

    projects = await discover_projects_async()
    proj = next((p for p in projects if p["project_id"] == project_id), None)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    if not proj["has_container_def"]:
        raise HTTPException(
            status_code=400,
            detail="Project has no Apptainer definition file (Apptainer/*.def)",
        )

    import secretbox

    registry = await db.get_setting("container_registry", "") or ""
    username = await db.get_setting("container_registry_username", "") or ""
    password = secretbox.decrypt(await db.get_setting("container_registry_password", "")) or ""

    from services.image_builder import stream_build

    async def event_stream():
        client_gone = False
        try:
            async for chunk in stream_build(
                project_id=project_id,
                project_dir=proj["project_dir"],
                registry=registry,
                username=username,
                password=password,
                tag=req.tag,
                push=req.push,
            ):
                yield chunk
        except GeneratorExit:
            # Client disconnected — skip the finally's [DONE] to avoid the
            # "async generator ignored GeneratorExit" noise (BE-9).
            client_gone = True
            raise
        finally:
            if not client_gone:
                yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Unified Run flow (#241, Phase 1) ──────────────────────────────────────
#
# One action replacing the Generate / Build / Plan / Run button row. The four
# endpoints above stay exactly as they are: Advanced mode still drives them
# individually, and nothing here changes their behaviour.


@router.post("/projects/{project_id}/run-flow")
async def start_run_flow(project_id: str, req: RunFlowRequest) -> dict:
    """Start the whole prepare-and-run sequence as one background flow.

    Returns as soon as the flow is created — progress arrives on
    `/run-flow/events`. The flow outlives the request that started it, so
    closing the tab doesn't strand it half-done.
    """
    from services.run_flow import manager

    projects = await discover_projects_async()
    proj = next((p for p in projects if p["project_id"] == project_id), None)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    try:
        flow = manager.start(
            project_id=project_id,
            project_dir=proj["project_dir"],
            generator_file=proj.get("generator_file") if proj["has_generator"] else None,
            has_container_def=bool(proj["has_container_def"]),
            generator_args=req.args,
            site=req.site,
            output_site=req.output_site,
            force_build=req.force_build,
        )
    except RuntimeError as e:
        # 409, not 400: the request is well-formed, the project is just busy.
        raise HTTPException(status_code=409, detail=str(e)) from e

    return flow.snapshot()


@router.get("/projects/{project_id}/run-flow")
async def get_run_flow(project_id: str) -> dict:
    """Current (or most recent) flow for a project, without streaming.

    How a reopened page finds out whether a run is in progress before deciding
    to attach to the event stream.
    """
    from services.run_flow import manager

    flow = manager.get(project_id)
    if flow is None:
        return {"project_id": project_id, "state": None, "active": False}
    return flow.snapshot()


@router.get("/projects/{project_id}/run-flow/events")
async def run_flow_events(project_id: str) -> StreamingResponse:
    """SSE stream of a flow: full replay, then live until it ends.

    Replaying rather than streaming-from-now is what makes the flow
    reattachable — a client that arrives late sees the same sequence as one
    present from the start.
    """
    from services.run_flow import manager

    flow = manager.get(project_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="No run in progress")

    async def event_stream():
        client_gone = False
        try:
            async for event in flow.subscribe():
                yield f"data: {json.dumps(event)}\n\n"
        except GeneratorExit:
            # Client disconnected — skip the [DONE] to avoid the "async
            # generator ignored GeneratorExit" noise (same as build, BE-9).
            client_gone = True
            raise
        finally:
            if not client_gone:
                yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/projects/{project_id}/plan")
async def plan_workflow(
    project_id: str, req: PlanRequest = PlanRequest()
) -> dict:
    """Run pegasus-plan (without --submit) on a project."""
    projects = await discover_projects_async()
    proj = next((p for p in projects if p["project_id"] == project_id), None)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    project_dir = proj["project_dir"]

    # Find workflow file
    from services.workflow_submitter import _find_workflow_file

    wf_file = _find_workflow_file(project_dir)
    if not wf_file:
        raise HTTPException(
            status_code=400,
            detail="No workflow YAML file found. Run generate first.",
        )

    cmd = ["pegasus-plan", "-s", req.site, "-o", req.output_site]
    from services.execution_sites import plan_output_dir

    output_dir = plan_output_dir(project_dir, req.output_site)
    if output_dir:
        cmd += ["--output-dir", output_dir]
    cmd.append(wf_file)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        stdout, stderr = await communicate_or_kill(proc, _CMD_TIMEOUT)
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        output = out
        if err:
            output += f"\nstderr:\n{err}"

        if proc.returncode == 0:
            # Capture the planned run dir so the Run action can submit it via
            # pegasus-run without re-planning.
            from services.workflow_submitter import (
                _extract_run_dir,
                _newest_run_dir,
            )

            run_dir = _extract_run_dir(out) or _newest_run_dir(project_dir)
            log.info("workflow_planned", project_id=project_id, run_dir=run_dir)
            return {"status": "planned", "output": output, "run_dir": run_dir or ""}
        else:
            return {"status": "error", "output": output}

    except FileNotFoundError:
        return {"status": "error", "output": "pegasus-plan not found"}
    except asyncio.TimeoutError:
        return {"status": "error", "output": "pegasus-plan timed out"}
    except Exception as e:
        return {"status": "error", "output": f"Error: {e}"}


async def _notify_run_started(result: dict) -> None:
    """Raise a 'run started' notification for a successful submit.

    Submit-time alert (independent of the poll-based state-transition engine),
    so every Run — including a re-run that produces no status change — surfaces
    at least one notification.
    """
    if result.get("status") != "submitted":
        return
    run_dir = result.get("run_dir")
    if not run_dir:
        return

    run_id = os.path.basename(run_dir)
    label = run_id
    bd_path = os.path.join(run_dir, "braindump.yml")
    if os.path.isfile(bd_path):
        try:
            from services.workflow_scanner import _parse_braindump

            bd = _parse_braindump(bd_path)
            run_id = bd.get("wf_uuid", run_id)
            wf_name = bd.get("pegasus_wf_name")
            run_name = os.path.basename(run_dir)
            label = (
                f"{wf_name} / {run_name}"
                if wf_name and wf_name != run_name
                else run_name
            )
        except Exception:
            pass

    try:
        from services.notification_engine import engine

        await engine.notify(
            kind="workflow_state",
            title=f"Workflow “{label}” run started",
            severity="info",
            run_id=run_id,
            link=f"/workflows?run={run_id}",
            # Refreshes on a re-run of the same dir (same wf_uuid) so it
            # re-surfaces; a fresh run gets a distinct uuid -> distinct alert.
            dedup_key=f"run-started:{run_id}",
        )
    except Exception as e:  # never let a notification failure break submit
        log.warning("run_started_notify_failed", run_dir=run_dir, error=str(e))

    # A submit may be the re-run that closes a feedback episode. Linked here
    # so the episode records the real submit time; the watch loop links it
    # too, from state, for runs submitted outside Studio.
    try:
        from main import db
        from services import feedback_episodes

        await feedback_episodes.observe_submit(db, run_dir)
    except Exception as e:  # noqa: BLE001 — instrumentation must not break submit
        log.warning("feedback_submit_observe_failed", run_dir=run_dir, error=str(e))


@router.post("/projects/{project_id}/submit")
async def submit_project(
    project_id: str, req: PlanRequest = PlanRequest()
) -> dict:
    """Run a planned workflow via pegasus-run (the "Run" action).

    Submits the dir a prior Plan produced instead of re-planning. Falls back to
    plan+submit if nothing has been planned yet.
    """
    projects = await discover_projects_async()
    proj = next((p for p in projects if p["project_id"] == project_id), None)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    result = await run_planned_workflow(
        proj["project_dir"],
        run_dir=req.run_dir,
        site=req.site,
        output_site=req.output_site,
    )
    await _notify_run_started(result)
    return {"project_id": project_id, **result}


@router.post("/submit")
async def submit(
    workflow_dir: str = Query(..., description="Path to workflow directory"),
    site: str = Query("condorpool", description="Execution site"),
    output_site: str = Query("local", description="Output site"),
) -> dict:
    """Plan and submit a Pegasus workflow."""
    # Validate path is in workspace
    resolved = os.path.realpath(os.path.expanduser(workflow_dir))
    root = os.path.realpath(WORKSPACE_ROOT)
    if not resolved.startswith(root + os.sep) and resolved != root:
        raise HTTPException(
            status_code=403,
            detail=f"Workflow dir must be inside {WORKSPACE_ROOT}",
        )

    result = await submit_workflow(resolved, site=site, output_site=output_site)
    await _notify_run_started(result)
    return result


# ── Run-specific endpoints (/{run_id} path parameter routes) ──


# YAML files that live next to a workflow but aren't one (catalogs etc.)
_NON_WORKFLOW_YML = {
    "braindump.yml", "braindump.yaml",
    "replicas.yml", "replicas.yaml", "rc.yml",
    "sites.yml", "sites.yaml",
    "transformations.yml", "transformations.yaml", "tc.yml",
}


def _project_workflow_file(project_dir: str) -> str | None:
    """The project's abstract workflow YAML, if generated."""
    candidates = sorted(
        glob.glob(os.path.join(project_dir, "*.yml"))
        + glob.glob(os.path.join(project_dir, "*.yaml"))
    )
    for f in candidates:
        if os.path.basename(f) in ("workflow.yml", "workflow.yaml"):
            return f
    for f in candidates:
        if os.path.basename(f) in _NON_WORKFLOW_YML:
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096)
        except OSError:
            continue
        if "pegasus:" in head and "jobs:" in head:
            return f
    return None


_DIGITS_RE = re.compile(r"\d+")


def _file_family(name: str) -> str:
    """What a file has in common with its siblings: digit runs normalised, so
    `gauge_01646500_daily.csv` and `gauge_06934500_daily.csv` group as
    `gauge_#_daily.csv` the way their producing steps group."""
    return _DIGITS_RE.sub("#", name)


def _add_file_nodes(doc: dict, nodes: list[dict], edges: list[dict]) -> list[dict]:
    """File nodes and their edges, for the graph's "Show files" view (#458).

    A file links from the job that writes it to each job that reads it. A
    job-to-job dependency is dropped where a file now carries it, so the same
    relationship isn't drawn twice; dependencies with no file behind them are
    kept. Returns the edges to use.
    """
    from services.run_outputs import classify

    producer: dict[str, tuple[str, bool]] = {}
    consumers: dict[str, list[str]] = {}
    order: list[str] = []
    for j in doc.get("jobs") or []:
        jid = str(j.get("id") or "")
        if not jid:
            continue
        for use in j.get("uses") or []:
            if not isinstance(use, dict) or not use.get("lfn"):
                continue
            lfn = str(use["lfn"])
            if lfn not in producer and lfn not in consumers:
                order.append(lfn)
            if str(use.get("type") or "input").lower() == "input":
                consumers.setdefault(lfn, []).append(jid)
            else:
                producer.setdefault(lfn, (jid, bool(use.get("stageOut", True))))

    carried: set[tuple[str, str]] = set()
    file_edges: list[dict] = []
    for lfn in order:
        fid = f"file:{lfn}"
        name = os.path.basename(lfn) or lfn
        made_by = producer.get(lfn)
        nodes.append({
            "id": fid,
            "label": name,
            "transformation": _file_family(name),
            "status": "file",
            "pegasus": False,
            "file": True,
            "lfn": lfn,
            "kind": classify(name),
            "result": bool(made_by and made_by[1]),
        })
        if made_by:
            file_edges.append({"source": made_by[0], "target": fid})
        for c in consumers.get(lfn, []):
            file_edges.append({"source": fid, "target": c})
            if made_by:
                carried.add((made_by[0], c))
    kept = [e for e in edges if (e["source"], e["target"]) not in carried]
    return kept + file_edges


def _parse_workflow_graph(wf_path: str, include_files: bool = False) -> dict:
    """Abstract workflow YAML -> {name, nodes, edges} for the client-side
    DAG visualizer (React Flow). Node ids are the abstract job ids; with
    `include_files`, files are nodes too (ids `file:<lfn>`)."""
    import yaml

    try:
        with open(wf_path, encoding="utf-8", errors="replace") as f:
            doc = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        raise HTTPException(
            status_code=500, detail=f"Could not parse workflow file: {e}"
        )

    nodes = []
    for j in doc.get("jobs") or []:
        jid = str(j.get("id") or "")
        if not jid:
            continue
        nodes.append({
            "id": jid,
            "label": str(j.get("name") or j.get("file") or jid),
            "transformation": j.get("name"),
            "status": "unsubmitted",
            "pegasus": False,  # abstract workflow holds only user jobs
        })
    edges = []
    for dep in doc.get("jobDependencies") or []:
        parent = str(dep.get("id") or "")
        for child in dep.get("children") or []:
            edges.append({"source": parent, "target": str(child)})
    if include_files:
        edges = _add_file_nodes(doc, nodes, edges)
    return {
        "name": doc.get("name") or os.path.basename(os.path.dirname(wf_path)),
        "nodes": nodes,
        "edges": edges,
    }


# Pegasus-injected auxiliary jobs (added by pegasus-plan, not in the abstract
# workflow): directory setup, data staging, cleanup, replica registration, chmod.
_PEGASUS_AUX_RE = re.compile(
    r"^(create_dir|stage_in|stage_out|stage_inter|stage_worker|"
    r"clean_up|cleanup|register|chmod|pegasus_)",
    re.IGNORECASE,
)


def _aux_label(job_id: str) -> str:
    """Short, friendly label for a Pegasus auxiliary job id."""
    m = _PEGASUS_AUX_RE.match(job_id)
    return m.group(1).lower().rstrip("_") if m else job_id


def _find_dag_file(run_dir: str) -> str | None:
    """The run's executable HTCondor DAG (e.g. <name>-0.dag) in the submit dir."""
    cands = [
        p for p in glob.glob(os.path.join(run_dir, "*.dag")) if os.path.isfile(p)
    ]
    return sorted(cands)[0] if cands else None


def _parse_executable_dag(dag_path: str) -> dict:
    """Executable DAGMan file -> {nodes, edges} including the Pegasus auxiliary
    jobs (stage-in/out, create-dir, cleanup, register). Each node carries a
    `pegasus` flag so the client can show/hide them."""
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()
    try:
        with open(dag_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                kw = parts[0].upper()
                if kw == "JOB" and len(parts) >= 2:
                    jid = parts[1]
                    if jid in seen:
                        continue
                    seen.add(jid)
                    aux = bool(_PEGASUS_AUX_RE.match(jid))
                    nodes.append({
                        "id": jid,
                        "label": _aux_label(jid) if aux else jid,
                        "transformation": None,
                        "status": "unsubmitted",
                        "pegasus": aux,
                    })
                elif kw == "PARENT" and "CHILD" in parts:
                    ci = parts.index("CHILD")
                    for p in parts[1:ci]:
                        for c in parts[ci + 1:]:
                            edges.append({"source": p, "target": c})
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"Could not parse DAG file: {e}"
        )
    return {"nodes": nodes, "edges": edges}


def _find_run_workflow_file(run_dir: str) -> str | None:
    """Abstract workflow YAML for a run: in the run dir or an enclosing project."""
    d = run_dir
    for _ in range(5):
        wf = _project_workflow_file(d)
        if wf:
            return wf
        parent = os.path.dirname(d)
        if parent == d or not parent.startswith(WORKSPACE_ROOT):
            break
        d = parent
    return None


@router.get("/projects/{project_id}/graph.json")
async def project_graph_json(project_id: str, files: bool = False) -> dict:
    """Workflow structure for the interactive DAG viewer (no run statuses)."""
    projects = await discover_projects_async()
    proj = next((p for p in projects if p["project_id"] == project_id), None)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    wf = _project_workflow_file(proj["project_dir"])
    if not wf:
        raise HTTPException(
            status_code=404, detail="No workflow file yet — run Generate first"
        )
    return _parse_workflow_graph(wf, include_files=files)


@router.get("/{run_id}/graph.json")
async def run_graph_json(
    run_id: str, include_pegasus: bool = False, files: bool = False
) -> dict:
    """Workflow structure with live job statuses joined from the run's
    workflow-events.jsonl (exec job ids look like <transform>_<abstract-id>).

    By default returns the abstract workflow (user jobs only). With
    `include_pegasus=true` returns the executable DAG, which also includes the
    Pegasus auxiliary jobs (stage-in/out, create-dir, cleanup, register)."""
    runs = await discover_runs_async()
    run = next((r for r in runs if r["run_id"] == run_id), None)
    if not run or not os.path.isdir(run["run_dir"]):
        raise HTTPException(status_code=404, detail=f"Workflow not found: {run_id}")

    dag = _find_dag_file(run["run_dir"]) if include_pegasus else None
    if dag:
        graph = _parse_executable_dag(dag)
        graph["name"] = os.path.basename(os.path.dirname(dag)) or run_id
    else:
        wf = _find_run_workflow_file(run["run_dir"])
        if not wf:
            raise HTTPException(
                status_code=404, detail="No workflow file for this run"
            )
        # Files exist only in the abstract workflow; the executable DAG view
        # above has none to show.
        graph = _parse_workflow_graph(wf, include_files=files)

    jobs = await get_workflow_jobs(run["run_dir"])
    by_node: dict[str, dict] = {n["id"]: n for n in graph["nodes"]}
    for j in jobs:
        exec_id = str(j.get("job_id") or "")
        node = by_node.get(exec_id)
        if node is None:
            for nid, n in by_node.items():
                if exec_id.endswith(f"_{nid}"):
                    node = n
                    break
        if node is not None:
            node["status"] = j.get("status") or node["status"]
            node["exec_job_id"] = exec_id
    # discover_runs() entries carry no "status" (BE-8), so run.get("status")
    # was always None and the DAG viewer couldn't tell running from done.
    # Derive it from the joined node statuses instead.
    graph["workflow_status"] = _derive_workflow_status(graph["nodes"])
    return graph


def _derive_workflow_status(nodes: list[dict]) -> str | None:
    """Roll per-node statuses up into a workflow status for the DAG viewer.
    Active (any node still in flight) wins over failed wins over succeeded;
    None when nothing has started or the graph is empty."""
    # File nodes have no run state; counting them would keep a finished run
    # from ever reading "succeeded".
    nodes = [n for n in nodes if not n.get("file")]
    statuses = {str(n.get("status") or "unsubmitted") for n in nodes}
    if not nodes:
        return None
    if statuses & {"pending", "queued", "running", "held"}:
        return "running"
    if "failed" in statuses:
        return "failed"
    if statuses <= {"succeeded"}:
        return "succeeded"
    return None


async def _resolve_run_dir(run_id: str) -> str:
    """Resolve a run's submit dir from the DB, falling back to a filesystem
    scan (BE-7).

    Several endpoints looked up ``run_dir`` in the DB only, so they 404'd for
    any run not yet upserted by ``GET /api/workflows`` — a fresh DB after a
    restart, a pruned row, or a direct deep link. This mirrors the fallback
    ``get_workflow``/``estimate`` already had. Raises 404 if the run is unknown
    or its directory is gone from disk."""
    from main import db

    run_dir = None
    for r in await db.list_workflow_runs():
        if r["run_id"] == run_id:
            run_dir = r["run_dir"]
            break
    if not run_dir:
        scanned = await discover_runs_async()
        run_dir = next(
            (r["run_dir"] for r in scanned if r["run_id"] == run_id), None
        )
    if not run_dir or not os.path.isdir(run_dir):
        raise HTTPException(status_code=404, detail=f"Workflow not found: {run_id}")
    return run_dir


@router.get("/{run_id}")
async def get_workflow(run_id: str) -> dict:
    """Get detailed workflow info by run ID."""
    run_dir = await _resolve_run_dir(run_id)
    detail = await get_workflow_detail(run_dir)
    detail["run_id"] = run_id
    detail["run_dir"] = run_dir
    return detail


@router.get("/{run_id}/jobs")
async def get_jobs(run_id: str) -> dict:
    """Get job-level status for a workflow."""
    run_dir = await _resolve_run_dir(run_id)
    jobs = await get_workflow_jobs(run_dir)
    return {"run_id": run_id, "jobs": jobs}


@router.get("/{run_id}/outputs")
async def get_run_outputs(
    run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    output_site: str = Query("local"),
) -> dict:
    """The files a run produced (#241, Phase 4).

    Studio had no notion of a run's results until this: a scientist runs an
    analysis to get files back, and finding them meant leaving for JupyterLab
    and knowing where Pegasus stages outputs. Paginated because a run's storage
    directory is unbounded.
    """
    from services import run_outputs
    from services.workflow_scanner import project_roots

    run_dir = await _resolve_run_dir(run_id)
    loop = asyncio.get_event_loop()
    # The walk touches the filesystem (NFS in production), so keep it off the
    # event loop — every other request and WebSocket shares it.
    result = await loop.run_in_executor(
        None,
        lambda: run_outputs.listing(
            run_dir,
            project_roots(),
            output_site=output_site,
            offset=offset,
            limit=limit,
        ),
    )
    return {"run_id": run_id, **result}


@router.get("/{run_id}/outputs/file")
async def get_run_output_file(
    run_id: str,
    path: str = Query(..., description="Path relative to the output directory"),
    download: bool = Query(False),
    head: bool = Query(False, description="Serve the start of a file too large to preview"),
    output_site: str = Query("local"),
):
    """Serve one output file, inline for preview or as an attachment.

    `path` is caller-supplied, so it is resolved strictly inside the run's
    output directory — see `run_outputs.safe_output_path`.
    """
    from fastapi.responses import FileResponse

    from services import run_outputs
    from services.workflow_scanner import project_roots

    run_dir = await _resolve_run_dir(run_id)
    output_dir, _ = run_outputs.resolve_output_dir(
        run_dir, project_roots(), output_site
    )
    if not output_dir:
        raise HTTPException(status_code=404, detail="No output directory for this run")

    try:
        resolved = run_outputs.safe_output_path(output_dir, path)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied") from None
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")

    # Refuse to inline a file big enough to hang the tab, unless the caller
    # asked for just its start (a partial preview, #460).
    partial = _too_large_or_head(resolved, download, head)
    if partial is not None:
        return partial

    return FileResponse(
        resolved,
        filename=os.path.basename(resolved) if download else None,
        content_disposition_type="attachment" if download else "inline",
        headers=run_outputs.serve_headers(resolved),
    )


# ── Files a job reads and writes (the graph's job panel) ──

_FileLocation = Literal["result", "scratch", "project"]


def _too_large_or_head(resolved: str, download: bool, head: bool):
    """None to serve the whole file, a head-of-file Response for a partial
    preview, or raise 413 for a file too big to show and not cuttable."""
    from fastapi.responses import Response

    from services import run_outputs

    if download:
        return None
    kind = run_outputs.classify(os.path.basename(resolved))
    size = os.path.getsize(resolved)
    if size <= run_outputs.preview_limit(kind):
        return None
    if head and run_outputs.can_preview(kind, size)[1]:
        # The start of a long table or log (#460). Plain text whatever the
        # extension, so a cut .json or .html is shown, never interpreted.
        return Response(
            content=run_outputs.read_head(resolved),
            media_type="text/plain; charset=utf-8",
            headers={"X-Preview-Partial": "1", "X-File-Size": str(size)},
        )
    raise HTTPException(
        status_code=413,
        detail="File is too large to preview — download it instead",
    )


def _serve_file(base: str | None, path: str, download: bool, head: bool = False):
    """One file under `base`, inline or as an attachment.

    Shared by the job panel's run and project variants. `path` is
    caller-supplied, so it is confined to `base` exactly as the Results tab's
    outputs are, and the same preview size ceiling applies.
    """
    from fastapi.responses import FileResponse

    from services import run_outputs

    if not base:
        raise HTTPException(status_code=404, detail="That location isn't available for this run")
    try:
        resolved = run_outputs.safe_output_path(base, path)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied") from None
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    partial = _too_large_or_head(resolved, download, head)
    if partial is not None:
        return partial
    return FileResponse(
        resolved,
        filename=os.path.basename(resolved) if download else None,
        content_disposition_type="attachment" if download else "inline",
        headers=run_outputs.serve_headers(resolved),
    )


async def _run_bases(run_dir: str, output_site: str) -> dict[str, str]:
    from services import job_files
    from services.workflow_scanner import project_roots

    loop = asyncio.get_event_loop()
    # Reads catalogs and stats files on NFS; keep it off the event loop.
    return await loop.run_in_executor(
        None, lambda: job_files.bases_for_run(run_dir, project_roots(), output_site)
    )


@router.get("/{run_id}/jobs/{job_id}/files")
async def run_job_files(run_id: str, job_id: str, output_site: str = Query("local")) -> dict:
    """A job's inputs and outputs, and where each can be opened now."""
    from services import job_files

    run_dir = await _resolve_run_dir(run_id)
    doc = job_files.workflow_doc(_find_run_workflow_file(run_dir))
    uses = job_files.job_uses(doc, job_id)
    if uses is None:
        # Pegasus's own jobs (stage-in, cleanup, ...) are not in workflow.yml.
        return {"run_id": run_id, "job_id": job_id, "job_found": False, "files": []}
    bases = await _run_bases(run_dir, output_site)
    loop = asyncio.get_event_loop()
    files = await loop.run_in_executor(
        None, lambda: job_files.describe(uses, bases, doc)
    )
    return {"run_id": run_id, "job_id": job_id, "job_found": True, "files": files}


def _file_uses(doc: dict, lfns: list[str]) -> list[dict]:
    """`describe()` input for files picked by name: an output if some job
    writes it (with that job's stageOut), otherwise an input."""
    made: dict[str, bool] = {}
    for j in doc.get("jobs") or []:
        for use in j.get("uses") or []:
            if (
                isinstance(use, dict)
                and use.get("lfn") in lfns
                and str(use.get("type") or "input").lower() != "input"
            ):
                made.setdefault(str(use["lfn"]), bool(use.get("stageOut", True)))
    return [
        {"lfn": lfn, "link": "output" if lfn in made else "input",
         "stage_out": made.get(lfn, False)}
        for lfn in dict.fromkeys(lfns)
    ]


@router.get("/{run_id}/files/info")
async def run_files_info(
    run_id: str,
    lfn: list[str] = Query(..., max_length=500),
    output_site: str = Query("local"),
) -> dict:
    """Where the given files are and whether they can be opened: a file node,
    or a group of them, clicked in the graph (#458)."""
    from services import job_files

    run_dir = await _resolve_run_dir(run_id)
    doc = job_files.workflow_doc(_find_run_workflow_file(run_dir))
    bases = await _run_bases(run_dir, output_site)
    uses = _file_uses(doc, lfn)
    loop = asyncio.get_event_loop()
    files = await loop.run_in_executor(None, lambda: job_files.describe(uses, bases, doc))
    return {"run_id": run_id, "job_found": True, "files": files}


@router.get("/{run_id}/files/content")
async def run_file_content(
    run_id: str,
    location: _FileLocation,
    path: str = Query(..., description="Path relative to the location's directory"),
    download: bool = Query(False),
    head: bool = Query(False, description="Serve the start of a file too large to preview"),
    output_site: str = Query("local"),
):
    run_dir = await _resolve_run_dir(run_id)
    bases = await _run_bases(run_dir, output_site)
    return _serve_file(bases.get(location), path, download, head)


async def _project_or_404(project_id: str) -> dict:
    projects = await discover_projects_async()
    proj = next((p for p in projects if p["project_id"] == project_id), None)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return proj


@router.get("/projects/{project_id}/jobs/{job_id}/files")
async def project_job_files(project_id: str, job_id: str) -> dict:
    """Before any run: what a job will read and write. Only inputs that are
    local files in the project exist yet, so only those can be previewed."""
    from services import job_files

    proj = await _project_or_404(project_id)
    doc = job_files.workflow_doc(_project_workflow_file(proj["project_dir"]))
    uses = job_files.job_uses(doc, job_id)
    if uses is None:
        return {"job_id": job_id, "job_found": False, "files": []}
    bases = {"project": os.path.realpath(proj["project_dir"])}
    files = job_files.describe(uses, bases, doc)
    return {"job_id": job_id, "job_found": True, "files": files}


@router.get("/projects/{project_id}/files/info")
async def project_files_info(
    project_id: str, lfn: list[str] = Query(..., max_length=500)
) -> dict:
    from services import job_files

    proj = await _project_or_404(project_id)
    doc = job_files.workflow_doc(_project_workflow_file(proj["project_dir"]))
    bases = {"project": os.path.realpath(proj["project_dir"])}
    files = job_files.describe(_file_uses(doc, lfn), bases, doc)
    return {"job_found": True, "files": files}


@router.get("/projects/{project_id}/files/content")
async def project_file_content(
    project_id: str,
    path: str = Query(...),
    download: bool = Query(False),
    head: bool = Query(False),
):
    proj = await _project_or_404(project_id)
    return _serve_file(os.path.realpath(proj["project_dir"]), path, download, head)


@router.get("/{run_id}/events")
async def workflow_events(run_id: str) -> StreamingResponse:
    """SSE stream of real-time workflow monitoring events."""
    run_dir = await _resolve_run_dir(run_id)

    async def event_stream():
        async for event in tail_workflow_events(run_dir):
            yield f"data: {json.dumps(event)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{run_id}/estimate")
async def estimate_workflow(run_id: str) -> dict:
    """Estimated remaining wall-clock for a run (historical-mean heuristic)."""
    run_dir = await _resolve_run_dir(run_id)

    from services.runtime_estimate import estimate_remaining

    loop = asyncio.get_event_loop()
    estimate = await loop.run_in_executor(None, estimate_remaining, run_dir)
    return {"run_id": run_id, "estimate": estimate}


@router.post("/{run_id}/analyze")
async def analyze_workflow(run_id: str) -> dict:
    """Run pegasus-analyzer on a workflow."""
    run_dir = await _resolve_run_dir(run_id)
    output = await run_pegasus_analyzer(run_dir)
    return {"run_id": run_id, "analysis": output}


@router.get("/{run_id}/diagnosis")
async def diagnose_run(run_id: str) -> dict:
    """Plain-language failure cards for a running/finished workflow (#241, P5).

    Phase 1 covers the pre-run stages; this covers the workflow itself, where
    the raw material is job stderr, exit codes and HTCondor hold reasons. Every
    card carries an action — naming a problem and stopping is the failure mode
    of the raw-stderr panel this replaces.

    Cheap and side-effect free (it reads what the monitor already collected),
    so it is safe to poll alongside the jobs table. `pegasus-analyzer` stays a
    separate, explicit action.
    """
    from services import run_diagnosis

    run_dir = await _resolve_run_dir(run_id)
    jobs = await get_workflow_jobs(run_dir)

    cards = run_diagnosis.diagnose_jobs(jobs)

    # Hold reasons come from the live queue, which is only meaningful while the
    # run still has jobs in it. A queue that isn't reachable is not an error
    # here — it just means no hold information to add.
    try:
        from services import condor_pool

        # get_status() is cached, so this rides the snapshot the dashboard and
        # Resources page already refresh rather than issuing its own condor_q.
        status = await condor_pool.get_status()
        demand = status.get("demand") or {}
        cards.extend(
            run_diagnosis.diagnose_holds(demand.get("held_reasons") or [])
        )
    except Exception as e:  # noqa: BLE001 — diagnosis is best-effort
        log.debug("diagnosis_hold_lookup_failed", run_id=run_id, error=str(e))

    return {"run_id": run_id, "cards": cards, "total": len(cards)}


@router.post("/{run_id}/cancel")
async def cancel_workflow_run(run_id: str) -> dict:
    """Take a run out of the Condor queue, keeping its directory on disk.

    Distinct from DELETE, which also removes the submit directory. A run that is
    stuck, mis-planned, or simply not wanted any more has to leave the queue
    before it consumes more slots — but its logs are the only record of why, so
    removing the queue entry must not destroy them. ``condor_rm`` on the DAG by
    hand orphans the Pegasus bookkeeping, so this goes through pegasus-remove.
    """
    from services.workflow_submitter import cancel_workflow

    run_dir = await _resolve_run_dir(run_id)
    result = await cancel_workflow(run_dir)
    if result.get("status") == "error":
        # Already finished or never queued is the common case, and is not a
        # server fault — report it as a failed request the UI can show verbatim.
        raise HTTPException(
            status_code=409,
            detail=result.get("output") or "Could not remove the run from the queue",
        )
    log.info("workflow_run_cancelled", run_id=run_id, run_dir=run_dir)
    return {"run_id": run_id, **result}


@router.delete("/{run_id}")
async def delete_workflow(run_id: str) -> dict:
    """Delete a specific run: cancel it if still active, then remove its submit
    directory from disk and drop it from the run list.

    Destructive — the UI confirms with the user first. Only ever deletes inside
    the scan root (the user's home tree), never an arbitrary path.
    """
    from main import db
    from services.workflow_scanner import SCAN_ROOT
    from services.workflow_submitter import delete_run

    # Resolve the run dir from the DB, falling back to a fresh scan.
    run_dir = None
    for r in await db.list_workflow_runs():
        if r["run_id"] == run_id:
            run_dir = r["run_dir"]
            break
    if not run_dir:
        for r in await discover_runs_async():
            if r["run_id"] == run_id:
                run_dir = r["run_dir"]
                break

    if not run_dir:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {run_id}")

    if os.path.isdir(run_dir):
        # Safety: confine deletion to the scan root.
        resolved = os.path.realpath(run_dir)
        root = os.path.realpath(SCAN_ROOT)
        if resolved != root and not resolved.startswith(root + os.sep):
            raise HTTPException(
                status_code=403, detail="Run directory is outside the workspace"
            )
        result = await delete_run(resolved)
    else:
        # Directory already gone — just clear any stale bookkeeping.
        result = {"status": "deleted"}

    await db.delete_workflow_run(run_id)
    return {"run_id": run_id, **result}
