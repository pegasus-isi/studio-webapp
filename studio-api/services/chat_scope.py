"""Workflow/run context for chat turns.

The chat panel lets the user pin a scope — a workflow project or one of its
runs — and sends it as ``{"type": "workflow"|"run", "id": …}`` with every
turn. This module resolves that scope into a system-prompt block so the
assistant answers about the thing the user is actually looking at ("why did
it fail?" needs no names).

Built fresh on every turn, server-side: status, job counts and failure
diagnosis reflect the run *now*, not whatever snapshot the browser happened
to hold when the user last clicked. Diagnosis-centric by design — the block
leads with the classified failure cards (`run_diagnosis`), because the
question users bring to a run is overwhelmingly "what went wrong".

Failure posture: a scope block is an enrichment, never a dependency. Every
resolver failure — unknown id, unreadable run dir, a service throwing — logs
and resolves to None, and the turn proceeds unscoped rather than erroring.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger()

SCOPE_TYPES = ("workflow", "run")

# Budget for the whole block, in characters (~1.5K tokens). Enough for counts,
# a handful of failure cards and the parent-project summary; small enough that
# an 8K-context model still has room for the conversation.
_MAX_BLOCK_CHARS = 6000

# Per-item caps, so one pathological job can't spend the whole budget.
_MAX_FAILURE_CARDS = 5
_MAX_STDERR_CHARS = 400
_MAX_RECENT_RUNS = 5
_MAX_README_CHARS = 800


def _clip(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …[truncated]"


async def build_scope_block(scope_type: Any, scope_id: Any) -> str | None:
    """The system-prompt block for a scope, or None if it can't be built.

    None on *any* failure — the chat turn must never break on context.
    """
    if scope_type not in SCOPE_TYPES or not scope_id or not isinstance(scope_id, str):
        return None
    try:
        if scope_type == "workflow":
            body = await _workflow_scope(scope_id)
        else:
            body = await _run_scope(scope_id)
    except Exception as e:  # noqa: BLE001 — enrichment, never a dependency
        log.warning(
            "chat_scope_build_failed",
            scope_type=scope_type,
            scope_id=scope_id,
            error=str(e),
        )
        return None
    if not body:
        log.info(
            "chat_scope_not_found", scope_type=scope_type, scope_id=scope_id
        )
        return None
    block = (
        "## Active Context\n\n"
        + body
        + "\n\nThe user selected this context in the chat panel. Center your"
        " answers on it: resolve bare references like \"this workflow\","
        " \"the run\" or \"why did it fail?\" against it, and prefer its"
        " directories when reading files or running commands. The user may"
        " still ask about anything else — answer those normally."
    )
    return _clip(block, _MAX_BLOCK_CHARS)


# --- workflow (project) scope ------------------------------------------------


async def _find_project(project_id: str) -> dict[str, Any] | None:
    from services.workflow_scanner import discover_projects_async

    projects = await discover_projects_async()
    return next(
        (p for p in projects if p.get("project_id") == project_id), None
    )


def _format_workflow(project: dict[str, Any], *, heading: str) -> str:
    lines = [
        f"{heading} Workflow: {project.get('name') or project.get('project_id')}",
        "",
        f"- Project id: {project.get('project_id')}",
        f"- Directory: {project.get('project_dir')}",
        f"- Status: {project.get('status')}",
    ]
    if project.get("generator_file"):
        lines.append(f"- Generator: {project['generator_file']}")
    runs = project.get("submitted_runs") or []
    if runs:
        lines.append(f"- Runs ({len(runs)}, newest first):")
        for r in runs[:_MAX_RECENT_RUNS]:
            lines.append(
                f"  - {r.get('run_name')} — {r.get('status')}"
                f" (run_id {r.get('run_id')})"
            )
        if len(runs) > _MAX_RECENT_RUNS:
            lines.append(f"  - … and {len(runs) - _MAX_RECENT_RUNS} more")
    else:
        lines.append("- Runs: none submitted yet")
    return "\n".join(lines)


def _read_readme_head(project_dir: str) -> str | None:
    import os

    for name in ("README.md", "README.rst", "README.txt", "README"):
        path = os.path.join(project_dir, name)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    return _clip(f.read(_MAX_README_CHARS + 1), _MAX_README_CHARS)
            except OSError:
                return None
    return None


async def _workflow_scope(project_id: str) -> str | None:
    project = await _find_project(project_id)
    if not project:
        return None
    parts = [_format_workflow(project, heading="###")]
    readme = _read_readme_head(project.get("project_dir") or "")
    if readme:
        parts.append("### Project README (head)\n\n" + readme)
    return "\n\n".join(parts)


# --- run scope ---------------------------------------------------------------


async def _resolve_run_dir(run_id: str) -> str | None:
    """DB row first (cheap), then a filesystem scan — the same order the
    workflows router uses, minus the HTTPException (scope failures are soft)."""
    import os

    from main import db
    from services.workflow_scanner import discover_runs_async

    run_dir = None
    try:
        for r in await db.list_workflow_runs():
            if r["run_id"] == run_id:
                run_dir = r["run_dir"]
                break
    except Exception:  # noqa: BLE001 — fall through to the scan
        run_dir = None
    if not run_dir:
        scanned = await discover_runs_async()
        run_dir = next(
            (r["run_dir"] for r in scanned if r["run_id"] == run_id), None
        )
    if not run_dir or not os.path.isdir(run_dir):
        return None
    return run_dir


async def _project_for_run_dir(run_dir: str) -> dict[str, Any] | None:
    """The project whose directory contains this run, if any."""
    import os

    from services.workflow_scanner import discover_projects_async

    run_dir = os.path.realpath(run_dir)
    for p in await discover_projects_async():
        project_dir = os.path.realpath(p.get("project_dir") or "")
        if project_dir and (run_dir + os.sep).startswith(project_dir + os.sep):
            return p
    return None


def _format_failure_cards(cards: list[dict[str, Any]]) -> list[str]:
    from services import run_diagnosis

    lines: list[str] = []
    for card in cards[:_MAX_FAILURE_CARDS]:
        step = card.get("step") or "(cluster)"
        lines.append(f"- **{step}**: {card.get('cause')}")
        if card.get("action"):
            lines.append(f"  - Suggested action: {card['action']}")
        if card.get("exitcode") not in (None, ""):
            lines.append(
                f"  - Exit code: {run_diagnosis.describe_exit(card['exitcode'])}"
            )
        technical = str(card.get("technical") or "").strip()
        if technical:
            lines.append(
                f"  - stderr: {_clip(technical, _MAX_STDERR_CHARS)}"
            )
    if len(cards) > _MAX_FAILURE_CARDS:
        lines.append(f"- … and {len(cards) - _MAX_FAILURE_CARDS} more failures")
    return lines


async def _run_scope(run_id: str) -> str | None:
    import os

    from services import run_diagnosis
    from services.workflow_monitor import get_workflow_detail, get_workflow_jobs

    run_dir = await _resolve_run_dir(run_id)
    if not run_dir:
        return None

    detail = await get_workflow_detail(run_dir)
    jobs = await get_workflow_jobs(run_dir)
    cards = run_diagnosis.diagnose_jobs(jobs)

    # Hold reasons ride the cached condor snapshot when it's reachable; a dead
    # or absent queue just means no hold cards, not a failed scope.
    try:
        from services import condor_pool

        status = await condor_pool.get_status()
        demand = status.get("demand") or {}
        cards.extend(run_diagnosis.diagnose_holds(demand.get("held_reasons") or []))
    except Exception:  # noqa: BLE001 — best-effort enrichment
        pass

    lines = [
        f"### Run: {os.path.basename(run_dir)}",
        "",
        f"- Run id: {run_id}",
        f"- Run directory: {run_dir}",
        f"- Status: {detail.get('status')}",
        (
            f"- Jobs: {detail.get('total_jobs', 0)} total,"
            f" {detail.get('completed_jobs', 0)} completed,"
            f" {detail.get('failed_jobs', 0)} failed,"
            f" {detail.get('running_jobs', 0)} running"
        ),
    ]
    if detail.get("exec_site"):
        lines.append(f"- Execution site: {detail['exec_site']}")

    if cards:
        lines.append("")
        lines.append("### Diagnosed failures")
        lines.extend(_format_failure_cards(cards))

    parts = ["\n".join(lines)]

    # A run's questions are usually also its workflow's questions ("does the
    # generator even produce that job?"), so the parent project rides along.
    project = await _project_for_run_dir(run_dir)
    if project:
        parts.append(_format_workflow(project, heading="### Parent"))

    return "\n\n".join(parts)
