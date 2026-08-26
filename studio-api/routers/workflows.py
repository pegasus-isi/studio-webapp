"""Workflow monitoring and management endpoints."""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re

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


class GenerateRequest(BaseModel):
    """CLI args (already flag-formatted) to pass to workflow_generator.py."""

    args: list[str] = Field(default_factory=list)


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

log = structlog.get_logger()

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)

_CMD_TIMEOUT = 300  # 5 minutes


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
            runs=[
                WorkflowProjectRunResponse(**r) for r in p["submitted_runs"]
            ],
        )
        for p in raw
    ]
    return WorkflowProjectListResponse(projects=projects)


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

    return {
        "project_id": project_id,
        "generator": generator,
        "plan": {"params": PLAN_PARAMS},
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
    cmd = ["python3", generator_file, *req.args]

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

    cmd = [
        "pegasus-plan",
        "-s", req.site,
        "-o", req.output_site,
        wf_file,
    ]

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


def _parse_workflow_graph(wf_path: str) -> dict:
    """Abstract workflow YAML -> {name, nodes, edges} for the client-side
    DAG visualizer (React Flow). Node ids are the abstract job ids."""
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
async def project_graph_json(project_id: str) -> dict:
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
    return _parse_workflow_graph(wf)


@router.get("/{run_id}/graph.json")
async def run_graph_json(run_id: str, include_pegasus: bool = False) -> dict:
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
        graph = _parse_workflow_graph(wf)

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
