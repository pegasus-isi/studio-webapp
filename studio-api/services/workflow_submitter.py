"""Workflow submitter — pegasus-plan --submit wrapper."""

from __future__ import annotations

import asyncio
import glob as _glob
import os
import re
import shutil
import sys
import time

import structlog

from services.backends import active_backend, backend_for_path

log = structlog.get_logger()

_CMD_TIMEOUT = 300  # 5 minutes


def _plan_property_flags() -> list[str]:
    """-D overrides injected into every studio-issued pegasus-plan.

    Pegasus properties files are NOT merged: the planner takes the FIRST of
    (--conf, ./pegasus.properties, ~/.pegasusrc), so a project that ships
    its own pegasus.properties silently shadows the user-level file — which
    is how the wfmonitor monitord plugin ended up disabled for a
    marketplace project despite ~/.pegasusrc enabling it. -D flags outrank
    every file and land in the plan-time snapshot monitord reads, so the
    event JSONL works for any studio-planned run. Where the wfmonitor
    adapter isn't installed, monitord logs an unmatched-name warning and
    carries on, so enabling it is safe everywhere.
    STUDIO_WFMONITOR_EVENTS=0 turns the injection off.
    """
    if os.environ.get("STUDIO_WFMONITOR_EVENTS", "").strip().lower() in (
        "0",
        "false",
        "no",
    ):
        return []
    return ["-Dpegasus.monitord.plugins.wfmonitor.enabled=true"]

# Deleting a run on NFS: a file a just-cancelled process still holds open
# lingers as a `.nfsXXXX` silly-rename that can't be unlinked until the handle
# closes ("Device or resource busy"). Retry a few times to let monitord/DAGMan
# exit after pegasus-remove before falling back to a best-effort delete.
_DELETE_RETRIES = 4
_DELETE_RETRY_DELAY = 1.5


async def submit_workflow(
    workflow_dir: str,
    site: str = "condorpool",
    output_site: str = "local",
) -> dict[str, str]:
    """Plan and submit a Pegasus workflow.

    Finds the workflow YAML/DAX file in the directory and runs
    pegasus-plan --submit.

    Returns dict with status, run_dir (if successful), and output.
    """
    if not os.path.isdir(workflow_dir):
        return {"status": "error", "output": f"Directory not found: {workflow_dir}"}

    # Find workflow file
    wf_file = _find_workflow_file(workflow_dir)
    if not wf_file:
        return {
            "status": "error",
            "output": f"No workflow file (.yml/.yaml/.dax) found in {workflow_dir}",
        }

    cmd = [
        "pegasus-plan",
        *_plan_property_flags(),
        "--submit",
        "-s", site,
        "-o", output_site,
        wf_file,
    ]

    try:
        result = await active_backend().exec(
            cmd, cwd=workflow_dir, timeout=_CMD_TIMEOUT
        )

        out = result.stdout.decode("utf-8", errors="replace")
        err = result.stderr.decode("utf-8", errors="replace")
        output = out
        if err:
            output += f"\nstderr:\n{err}"

        if result.returncode == 0:
            # Try to extract run directory from output
            run_dir = _extract_run_dir(out) or _newest_run_dir(workflow_dir)
            log.info(
                "workflow_submitted",
                workflow_dir=workflow_dir,
                run_dir=run_dir,
                site=site,
            )
            # The integrated pegasus-monitord writes workflow-events.jsonl on
            # its own, so there's no separate monitor to start here.
            return {
                "status": "submitted",
                "run_dir": run_dir or "",
                "output": output,
            }
        else:
            log.warning(
                "workflow_submit_failed",
                workflow_dir=workflow_dir,
                returncode=result.returncode,
            )
            return {"status": "error", "output": output}

    except FileNotFoundError:
        return {"status": "error", "output": "pegasus-plan not found"}
    except asyncio.TimeoutError:
        return {"status": "error", "output": "pegasus-plan timed out"}
    except Exception as e:
        return {"status": "error", "output": f"Error: {e}"}


async def run_planned_workflow(
    workflow_dir: str,
    run_dir: str | None = None,
    site: str = "condorpool",
    output_site: str = "local",
) -> dict[str, str]:
    """Submit an already-planned run directory via ``pegasus-run``.

    This is the "Run" action: it submits the workflow that a prior "Plan"
    produced, instead of re-planning. ``run_dir`` is the dir returned by Plan;
    if it's missing or invalid (e.g. the page was refreshed), fall back to the
    most recent planned dir under ``workflow_dir``. If no planned dir exists at
    all, fall back to a full plan+submit so Run still works standalone.

    Returns dict with status, run_dir (if successful), and output.
    """
    target: str | None = None
    if run_dir:
        candidate = os.path.realpath(os.path.expanduser(run_dir))
        # Reuse the freshly-planned dir from Plan only if it hasn't already
        # been submitted — pegasus-run on an already-running/submitted dir
        # fails with "workflow already running (remove monitord.pid)".
        if (
            os.path.isdir(candidate)
            and os.path.isfile(os.path.join(candidate, "braindump.yml"))
            and not _is_submitted(candidate)
        ):
            target = candidate

    # No reusable planned dir (no Plan handshake for this run, or it was
    # already submitted) — plan and submit a fresh run. We never re-run an
    # existing dir, which would either hit "workflow already running" or
    # re-submit a stale plan.
    if target is None:
        return await submit_workflow(
            workflow_dir, site=site, output_site=output_site
        )

    try:
        result = await backend_for_path(target).exec(
            ["pegasus-run", target], timeout=_CMD_TIMEOUT
        )
        out = result.stdout.decode("utf-8", errors="replace")
        err = result.stderr.decode("utf-8", errors="replace")
        output = out + (f"\nstderr:\n{err}" if err else "")

        if result.returncode == 0:
            log.info("workflow_run_submitted", run_dir=target, site=site)
            return {"status": "submitted", "run_dir": target, "output": output}

        log.warning(
            "workflow_run_failed", run_dir=target, returncode=result.returncode
        )
        return {"status": "error", "run_dir": target, "output": output}

    except FileNotFoundError:
        return {"status": "error", "output": "pegasus-run not found"}
    except asyncio.TimeoutError:
        return {"status": "error", "output": "pegasus-run timed out"}
    except Exception as e:
        return {"status": "error", "output": f"Error: {e}"}


async def cancel_workflow(run_dir: str) -> dict[str, str]:
    """Cancel a running workflow using pegasus-remove."""
    try:
        result = await backend_for_path(run_dir).exec(
            ["pegasus-remove", run_dir], timeout=60
        )
        output = result.stdout.decode("utf-8", errors="replace")
        if result.stderr:
            output += "\n" + result.stderr.decode("utf-8", errors="replace")

        if result.returncode == 0:
            log.info("workflow_cancelled", run_dir=run_dir)
            return {"status": "cancelled", "output": output}
        return {"status": "error", "output": output}

    except FileNotFoundError:
        return {"status": "error", "output": "pegasus-remove not found"}
    except asyncio.TimeoutError:
        return {"status": "error", "output": "pegasus-remove timed out"}
    except Exception as e:
        return {"status": "error", "output": f"Error: {e}"}


def _best_effort_rmtree(run_dir: str) -> list[str]:
    """Remove everything under ``run_dir`` that isn't held open; return the
    paths that could not be removed (NFS ``.nfsXXXX`` silly-renames of files a
    live process still has open)."""
    held: list[str] = []

    if sys.version_info >= (3, 12):
        # onexc replaced the deprecated onerror in 3.12 (prod runs 3.12); it
        # passes the exception instead of an exc_info tuple.
        def _onexc(func, path, exc):  # noqa: ANN001 - shutil callback shape
            held.append(path)

        shutil.rmtree(run_dir, onexc=_onexc)
    else:
        def _onerror(func, path, exc_info):  # noqa: ANN001 - shutil callback shape
            held.append(path)

        shutil.rmtree(run_dir, onerror=_onerror)
    return held


async def delete_run(run_dir: str) -> dict[str, str]:
    """Delete a run: cancel it if still active, then remove its submit dir.

    Best-effort ``pegasus-remove`` first (so a live DAG leaves the Condor queue),
    then ``rmtree`` the directory. The caller MUST validate that ``run_dir`` is
    inside the workspace before calling — this deletes files on disk.

    On NFS, a file a just-cancelled process still holds open lingers as a
    ``.nfsXXXX`` file that can't be unlinked yet, so ``rmtree`` is retried a few
    times to let those processes exit. If some files are still held after that,
    we remove everything else (crucially ``braindump.yml``, so the run drops off
    the list) and report success — the leftover stubs clear when the holder
    exits.
    """
    backend = backend_for_path(run_dir)
    if backend.is_remote:
        try:
            async with backend.project_operation():
                snapshot = await backend.deletion_snapshot(run_dir)
                from services.workflow_scanner import dagman_exit_status, _jobstate_terminal_status

                terminal = dagman_exit_status(snapshot) or _jobstate_terminal_status(snapshot)
                if _is_submitted(snapshot) and not terminal:
                    result = await backend.exec(["pegasus-remove", run_dir], timeout=60)
                    if result.returncode:
                        return {"status": "error", "output": result.stderr.decode(errors="replace") or "Could not cancel remote run"}
                await backend.delete_tree(run_dir)
            return {"status": "deleted"}
        except (OSError, RuntimeError, ValueError, asyncio.TimeoutError) as e:
            return {"status": "error", "output": str(e)}

    if _is_submitted(run_dir):
        try:
            await backend.exec(["pegasus-remove", run_dir], timeout=60)
        except (FileNotFoundError, asyncio.TimeoutError, OSError) as e:
            # A cancel failure shouldn't block deletion — log and proceed.
            log.warning("delete_run_cancel_failed", run_dir=run_dir, error=str(e))

    loop = asyncio.get_event_loop()
    last_err: OSError | None = None
    for attempt in range(_DELETE_RETRIES):
        try:
            await loop.run_in_executor(None, lambda: shutil.rmtree(run_dir))
            log.info("run_deleted", run_dir=run_dir)
            return {"status": "deleted"}
        except FileNotFoundError:
            return {"status": "deleted"}  # already gone
        except OSError as e:
            last_err = e
            if attempt < _DELETE_RETRIES - 1:
                await asyncio.sleep(_DELETE_RETRY_DELAY)

    # Still blocked. Remove everything we can and check whether the run is now
    # undiscoverable (its braindump.yml is gone) — if so, it's effectively
    # deleted; only NFS-held stubs remain and will clear on their own.
    held = await loop.run_in_executor(None, _best_effort_rmtree, run_dir)
    if not os.path.exists(os.path.join(run_dir, "braindump.yml")):
        log.warning("run_deleted_partial", run_dir=run_dir, held=len(held))
        return {"status": "deleted"}

    log.warning("run_delete_blocked", run_dir=run_dir, error=str(last_err))
    return {
        "status": "error",
        "output": (
            "Could not delete the run — files are still held open by a running "
            "process (NFS 'device or resource busy'). Stop/cancel the run and "
            f"try again once it has finished. ({last_err})"
        ),
    }


async def delete_project(project_dir: str) -> dict[str, str]:
    """Delete a whole workflow project: its runs, then the directory itself.

    The caller MUST validate that ``project_dir`` is inside the workflows
    directory before calling — this deletes files on disk.

    Runs are deleted first, individually, rather than by rmtree'ing the project
    outright. That is not tidiness: a run still in the Condor queue whose submit
    directory vanishes leaves jobs that reference files that no longer exist,
    and no way to reach them. ``delete_run`` already cancels before removing, so
    routing every run through it means the queue is always left consistent.

    A run that cannot be deleted (files held open on NFS) aborts the whole
    operation rather than pressing on — a half-deleted project with live jobs
    is a worse state than the one we started in, and the user can retry once
    the holder exits.
    """
    backend = backend_for_path(project_dir)
    if backend.is_remote:
        try:
            async with backend.project_operation():
                snapshot = await backend.deletion_snapshot(project_dir)
                for bd in sorted(_glob.glob(os.path.join(snapshot, "**", "braindump.yml"), recursive=True)):
                    run_dir = os.path.join(project_dir, os.path.relpath(os.path.dirname(bd), snapshot))
                    result = await delete_run(run_dir)
                    if result.get("status") != "deleted":
                        return result
                await backend.delete_tree(project_dir)
            return {"status": "deleted"}
        except (OSError, RuntimeError, ValueError, asyncio.TimeoutError) as e:
            return {"status": "error", "output": str(e)}

    runs = sorted(
        os.path.dirname(bd)
        for bd in _glob.glob(
            os.path.join(project_dir, "**", "braindump.yml"), recursive=True
        )
    )
    for run_dir in runs:
        result = await delete_run(run_dir)
        if result.get("status") != "deleted":
            log.warning(
                "project_delete_blocked_by_run",
                project_dir=project_dir, run_dir=run_dir,
            )
            return {
                "status": "error",
                "output": (
                    "Couldn't delete this workflow because one of its runs "
                    "could not be removed yet. "
                    + str(result.get("output") or "")
                ).strip(),
            }

    loop = asyncio.get_event_loop()
    last_err: OSError | None = None
    for attempt in range(_DELETE_RETRIES):
        try:
            await loop.run_in_executor(None, lambda: shutil.rmtree(project_dir))
            log.info("project_deleted", project_dir=project_dir, runs=len(runs))
            return {"status": "deleted"}
        except FileNotFoundError:
            return {"status": "deleted"}  # already gone
        except OSError as e:
            last_err = e
            if attempt < _DELETE_RETRIES - 1:
                await asyncio.sleep(_DELETE_RETRY_DELAY)

    # Same NFS reasoning as delete_run: strip what we can, and treat the
    # project as gone once nothing is left for the scanner to find.
    held = await loop.run_in_executor(None, _best_effort_rmtree, project_dir)
    if not os.path.isdir(project_dir) or not os.listdir(project_dir):
        log.warning(
            "project_deleted_partial", project_dir=project_dir, held=len(held)
        )
        return {"status": "deleted"}

    log.warning(
        "project_delete_blocked", project_dir=project_dir, error=str(last_err)
    )
    return {
        "status": "error",
        "output": (
            "Could not delete this workflow — some of its files are still held "
            "open by a running process. Try again in a moment. "
            f"({last_err})"
        ),
    }


# YAML files that are catalogs / aux config — never the abstract workflow.
# Feeding any of these to pegasus-plan triggers
# "Illegal key <...> for element workflow".
_NON_WORKFLOW_YML = {
    "braindump.yml", "braindump.yaml",
    "sites.yml", "sites.yaml",
    "transformations.yml", "transformations.yaml",
    "replicas.yml", "replicas.yaml",
    "pegasus.properties",
}


def _is_abstract_workflow(path: str) -> bool:
    """True if a YAML file is a Pegasus abstract workflow (DAX).

    The abstract workflow has a top-level ``jobs`` key; the site /
    transformation / replica catalogs do not.
    """
    try:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        return isinstance(data, dict) and "jobs" in data
    except Exception:
        return False


def _find_workflow_file(workflow_dir: str) -> str | None:
    """Find the abstract workflow (DAX) file in a directory.

    Earlier this returned the first ``*.yml`` glob match, which is in
    arbitrary filesystem order — so a catalog (e.g. transformations.yml) could
    be handed to pegasus-plan and fail. Now we (1) prefer the canonical
    ``workflow.yml``/``.yaml`` name, (2) then any ``*.dax``, (3) then the first
    YAML whose content is actually an abstract workflow — never a catalog.
    """
    # 1. Canonical workflow filename
    for name in ("workflow.yml", "workflow.yaml"):
        candidate = os.path.join(workflow_dir, name)
        if os.path.isfile(candidate):
            return candidate

    # 2. Explicit .dax files
    dax_files = sorted(_glob.glob(os.path.join(workflow_dir, "*.dax")))
    if dax_files:
        return dax_files[0]

    # 3. Content-detect: skip known catalogs, return the first real workflow
    candidates = sorted(
        _glob.glob(os.path.join(workflow_dir, "*.yml"))
        + _glob.glob(os.path.join(workflow_dir, "*.yaml"))
    )
    for match in candidates:
        if os.path.basename(match) in _NON_WORKFLOW_YML:
            continue
        if _is_abstract_workflow(match):
            return match
    return None


_RUN_DIR_RE = re.compile(r"(/[^\s\"']+/run\d{4})\b")


def _extract_run_dir(output: str) -> str | None:
    """Extract the run directory path from pegasus-plan output.

    Pegasus 5.x prefixes every output line with a timestamp + level
    ("2026.06.12 ... [INFO] ..."), so a bare path never starts a line —
    scan for /.../runNNNN tokens anywhere instead, keeping the last one
    that exists on disk (the final summary repeats it).
    """
    candidates = [
        m.group(1)
        for m in _RUN_DIR_RE.finditer(output)
        if os.path.isdir(m.group(1))
    ]
    if candidates:
        return candidates[-1]
    # Legacy format: a bare path on its own line
    for line in output.split("\n"):
        line = line.strip()
        if line.startswith("/") and "run" in line.lower() and os.path.isdir(line):
            return line
    return None


def _newest_run_dir(workflow_dir: str) -> str | None:
    """Filesystem fallback: the just-created run dir (fresh braindump.yml)."""
    candidates: list[tuple[float, str]] = []
    for bd in _glob.glob(
        os.path.join(workflow_dir, "**", "braindump.yml"), recursive=True
    ):
        try:
            mtime = os.path.getmtime(bd)
        except OSError:
            continue
        if time.time() - mtime < 600:
            candidates.append((mtime, os.path.dirname(bd)))
    return max(candidates)[1] if candidates else None


def _is_submitted(run_dir: str) -> bool:
    """True if a run dir has already been submitted (DAGMan / monitord ran).

    pegasus-run refuses such a dir ("workflow already running — remove the
    monitord.pid file"), so the Run action must not re-run it. A plan-only dir
    (pegasus-plan without --submit) has none of these markers.
    """
    if os.path.exists(os.path.join(run_dir, "monitord.pid")) or os.path.exists(
        os.path.join(run_dir, "monitord.started")
    ):
        return True
    return bool(_glob.glob(os.path.join(run_dir, "*.dag.dagman.out")))
