"""Workflow submitter — pegasus-plan --submit wrapper."""

from __future__ import annotations

import asyncio
import glob as _glob
import os
import re
import time

import structlog

log = structlog.get_logger()

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)

_CMD_TIMEOUT = 300  # 5 minutes


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
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_CMD_TIMEOUT
        )

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
            # Start the workflow-monitor server so it continuously writes
            # workflow-events.jsonl — the studio drives all status/job views
            # from that log.
            if run_dir:
                await _start_monitor(run_dir)
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


async def _start_monitor(run_dir: str) -> None:
    """Launch `workflow-monitor --serve` for a run dir (best-effort).

    The monitor daemonizes (double-fork) and writes
    ``<submit_dir>/workflow-events.jsonl`` continuously. Failure to start is
    logged but never blocks submission.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "workflow-monitor", "--serve", run_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # --serve double-forks and the launcher exits promptly; just reap it.
        await asyncio.wait_for(proc.communicate(), timeout=30)
        log.info("workflow_monitor_started", run_dir=run_dir)
    except FileNotFoundError:
        log.warning("workflow_monitor_missing", run_dir=run_dir)
    except Exception as e:
        log.warning("workflow_monitor_start_failed", run_dir=run_dir, error=str(e))


async def cancel_workflow(run_dir: str) -> dict[str, str]:
    """Cancel a running workflow using pegasus-remove."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pegasus-remove", run_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
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
