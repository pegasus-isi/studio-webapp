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

from services.procutil import communicate_or_kill

log = structlog.get_logger()

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)

_CMD_TIMEOUT = 300  # 5 minutes

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
        "--submit",
        "-s", site,
        "-o", output_site,
        wf_file,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workflow_dir,
        )
        stdout, stderr = await communicate_or_kill(proc, _CMD_TIMEOUT)

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        output = out
        if err:
            output += f"\nstderr:\n{err}"

        if proc.returncode == 0:
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
                returncode=proc.returncode,
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
        proc = await asyncio.create_subprocess_exec(
            "pegasus-run", target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await communicate_or_kill(proc, _CMD_TIMEOUT)
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        output = out + (f"\nstderr:\n{err}" if err else "")

        if proc.returncode == 0:
            log.info("workflow_run_submitted", run_dir=target, site=site)
            return {"status": "submitted", "run_dir": target, "output": output}

        log.warning(
            "workflow_run_failed", run_dir=target, returncode=proc.returncode
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
        proc = await asyncio.create_subprocess_exec(
            "pegasus-remove", run_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await communicate_or_kill(proc, 60)
        output = stdout.decode("utf-8", errors="replace")
        if stderr:
            output += "\n" + stderr.decode("utf-8", errors="replace")

        if proc.returncode == 0:
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
    if _is_submitted(run_dir):
        try:
            proc = await asyncio.create_subprocess_exec(
                "pegasus-remove", run_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await communicate_or_kill(proc, 60)
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
