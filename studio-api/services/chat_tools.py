"""Pegasus-aware tools for the builtin chat.

Read-only wrappers over the same services the Workflows UI uses
(workflow_scanner, workflow_monitor, run_diagnosis, run_outputs), exposed to
the model as tools. Before these, answering "what's the status?" meant the
model shelling out to `pegasus-status` through run_command and parsing text;
now it reads the same structured data the UI shows.

Scope defaulting is the point: with an active chat context (see chat_scope),
`run_id`/`project_id` fall back to the scoped entity, so "why did it fail?"
becomes `diagnose_run()` with no arguments. Without a scope the tools ask for
an explicit id in their error text — the model relays that instead of
guessing.

Everything here is read-only and confined to what the run/project directories
already expose, so none of it goes through the shell-approval gate.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

log = structlog.get_logger()

# Bounds so one tool result can't flood the model context. chat.py clips again
# on top of these; the caps here keep the *structure* useful (drop rows, not
# the closing brace).
_MAX_RUNS = 50
_MAX_JOBS = 100
_MAX_OUTPUT_FILES = 50
_MAX_CARD_STDERR = 1000

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_workflow_projects",
            "description": (
                "List the user's Pegasus workflow projects (name, directory,"
                " status, runs)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workflow_runs",
            "description": (
                "List Pegasus workflow runs with live status and job counts."
                " With an active workflow context, defaults to that project's"
                " runs; pass project_id to filter explicitly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": (
                            "Only runs belonging to this project (optional;"
                            " defaults to the active workflow context)"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_run_status",
            "description": (
                "Live status and job counts for one workflow run. run_id"
                " defaults to the run in the active chat context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "Workflow run id (optional with a run context)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_run_jobs",
            "description": (
                "Job-level detail for one workflow run (id, transformation,"
                " status, exit code, site, duration). run_id defaults to the"
                " run in the active chat context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "Workflow run id (optional with a run context)",
                    },
                    "status": {
                        "type": "string",
                        "description": (
                            "Only jobs in this state, e.g. 'failed' or"
                            " 'running' (optional)"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_run",
            "description": (
                "Classified failure diagnosis for one workflow run: each"
                " failed job with plain-language cause, suggested action, exit"
                " code and stderr excerpt, plus any HTCondor hold reasons."
                " The right first call for 'why did it fail?'. run_id defaults"
                " to the run in the active chat context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "Workflow run id (optional with a run context)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_run_outputs",
            "description": (
                "The files a workflow run produced (name, size, path). run_id"
                " defaults to the run in the active chat context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {
                        "type": "string",
                        "description": "Workflow run id (optional with a run context)",
                    },
                },
                "required": [],
            },
        },
    },
]

TOOL_NAMES = {t["function"]["name"] for t in TOOL_DEFINITIONS}


def _scoped(scope: dict[str, Any] | None, scope_type: str) -> str | None:
    if scope and scope.get("type") == scope_type and scope.get("id"):
        return str(scope["id"])
    return None


def _need_run_id() -> str:
    return (
        "Error: no run_id given and no run is selected in the chat context."
        " Pass run_id explicitly (list_workflow_runs shows them), or ask the"
        " user to pick a run in the context selector above the chat input."
    )


async def _run_dir_for(run_id: str) -> str | None:
    from services.chat_scope import _resolve_run_dir

    return await _resolve_run_dir(run_id)


def _compact_run_row(run: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run.get("run_id"),
        "run_name": run.get("run_name"),
        "workflow": run.get("name"),
        "run_dir": run.get("run_dir"),
        "status": detail.get("status"),
        "total_jobs": detail.get("total_jobs", 0),
        "completed_jobs": detail.get("completed_jobs", 0),
        "failed_jobs": detail.get("failed_jobs", 0),
        "running_jobs": detail.get("running_jobs", 0),
    }


async def execute(
    name: str, arguments: dict[str, Any], scope: dict[str, Any] | None = None
) -> str | None:
    """Run one Pegasus tool; None means "not one of ours" (caller falls
    through to the generic file/shell tools). Errors come back as strings —
    the model can read them and adjust, same contract as _execute_tool."""
    if name not in TOOL_NAMES:
        return None
    try:
        if name == "list_workflow_projects":
            return await _list_projects()
        if name == "list_workflow_runs":
            project_id = arguments.get("project_id") or _scoped(scope, "workflow")
            return await _list_runs(project_id)

        # Everything below is per-run.
        run_id = arguments.get("run_id") or _scoped(scope, "run")
        if not run_id:
            return _need_run_id()
        run_dir = await _run_dir_for(run_id)
        if not run_dir:
            return f"Error: no workflow run found with run_id {run_id!r}."

        if name == "get_run_status":
            return await _run_status(run_id, run_dir)
        if name == "get_run_jobs":
            return await _run_jobs(run_dir, arguments.get("status"))
        if name == "diagnose_run":
            return await _diagnose(run_dir)
        if name == "list_run_outputs":
            return await _outputs(run_dir)
    except Exception as e:  # noqa: BLE001 — a broken tool must answer, not raise
        log.warning("chat_tool_failed", tool=name, error=str(e))
        return f"Error executing {name}: {e}"
    return None


async def _list_projects() -> str:
    from services.workflow_scanner import discover_projects_async

    projects = await discover_projects_async()
    rows = [
        {
            "project_id": p.get("project_id"),
            "name": p.get("name"),
            "project_dir": p.get("project_dir"),
            "status": p.get("status"),
            "generator_file": p.get("generator_file"),
            "runs": [
                {
                    "run_id": r.get("run_id"),
                    "run_name": r.get("run_name"),
                    "status": r.get("status"),
                }
                for r in (p.get("submitted_runs") or [])[:_MAX_RUNS]
            ],
        }
        for p in projects
    ]
    return json.dumps({"projects": rows}, indent=1)


async def _list_runs(project_id: str | None) -> str:
    import os

    from services.workflow_monitor import get_workflow_detail
    from services.workflow_scanner import discover_runs_async

    runs = await discover_runs_async()

    if project_id:
        from services.workflow_scanner import discover_projects_async

        project = next(
            (
                p
                for p in await discover_projects_async()
                if p.get("project_id") == project_id
            ),
            None,
        )
        if not project:
            return f"Error: no workflow project found with project_id {project_id!r}."
        project_dir = os.path.realpath(project.get("project_dir") or "")
        runs = [
            r
            for r in runs
            if os.path.realpath(r.get("run_dir") or "").startswith(
                project_dir + os.sep
            )
        ]

    truncated = len(runs) > _MAX_RUNS
    rows = []
    for run in runs[:_MAX_RUNS]:
        detail = await get_workflow_detail(run["run_dir"])
        rows.append(_compact_run_row(run, detail))
    payload: dict[str, Any] = {"runs": rows}
    if project_id:
        payload["project_id"] = project_id
    if truncated:
        payload["note"] = f"showing {_MAX_RUNS} of {len(runs)} runs"
    return json.dumps(payload, indent=1)


async def _run_status(run_id: str, run_dir: str) -> str:
    from services.workflow_monitor import get_workflow_detail

    detail = await get_workflow_detail(run_dir)
    return json.dumps(
        {
            "run_id": run_id,
            "run_dir": run_dir,
            "status": detail.get("status"),
            "total_jobs": detail.get("total_jobs", 0),
            "completed_jobs": detail.get("completed_jobs", 0),
            "failed_jobs": detail.get("failed_jobs", 0),
            "running_jobs": detail.get("running_jobs", 0),
            "exec_site": detail.get("exec_site"),
        },
        indent=1,
    )


async def _run_jobs(run_dir: str, status_filter: Any) -> str:
    from services.workflow_monitor import get_workflow_jobs

    jobs = await get_workflow_jobs(run_dir)
    if status_filter:
        wanted = str(status_filter).lower()
        jobs = [j for j in jobs if str(j.get("status") or "").lower() == wanted]
    truncated = len(jobs) > _MAX_JOBS
    rows = [
        {
            "job_id": j.get("job_id"),
            "transformation": j.get("transformation"),
            "status": j.get("status"),
            "exitcode": j.get("exitcode"),
            "site": j.get("site"),
            "duration": j.get("duration"),
        }
        for j in jobs[:_MAX_JOBS]
    ]
    payload: dict[str, Any] = {"jobs": rows}
    if truncated:
        payload["note"] = f"showing {_MAX_JOBS} of {len(jobs)} jobs"
    return json.dumps(payload, indent=1)


async def _diagnose(run_dir: str) -> str:
    from services import run_diagnosis
    from services.workflow_monitor import get_workflow_detail, get_workflow_jobs

    detail = await get_workflow_detail(run_dir)
    jobs = await get_workflow_jobs(run_dir)
    cards = run_diagnosis.diagnose_jobs(jobs)

    # Hold reasons ride the cached condor snapshot; an unreachable queue means
    # no hold cards, not a failed diagnosis.
    try:
        from services import condor_pool

        status = await condor_pool.get_status()
        demand = status.get("demand") or {}
        cards.extend(
            run_diagnosis.diagnose_holds(demand.get("held_reasons") or [])
        )
    except Exception:  # noqa: BLE001 — best-effort enrichment
        pass

    for card in cards:
        technical = str(card.get("technical") or "")
        if len(technical) > _MAX_CARD_STDERR:
            card["technical"] = technical[:_MAX_CARD_STDERR] + " …[truncated]"

    return json.dumps(
        {
            "status": detail.get("status"),
            "failed_jobs": detail.get("failed_jobs", 0),
            "cards": cards,
            "hint": (
                "Each card names the failed step, a plain-language cause and a"
                " suggested action; 'technical' is the stderr excerpt."
            ),
        },
        indent=1,
    )


async def _outputs(run_dir: str) -> str:
    from services import run_outputs
    from services.workflow_scanner import WORKFLOWS_DIR

    loop = asyncio.get_event_loop()
    # The listing walks the filesystem (NFS in production) — keep it off the
    # event loop, same as the /outputs endpoint.
    result = await loop.run_in_executor(
        None,
        lambda: run_outputs.listing(
            run_dir, WORKFLOWS_DIR, offset=0, limit=_MAX_OUTPUT_FILES
        ),
    )
    return json.dumps(result, indent=1)
