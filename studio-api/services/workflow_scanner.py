"""Workflow scanner — discovers Pegasus run directories in the workspace.

Scans for braindump.yml files and extracts workflow metadata.
"""

from __future__ import annotations

import glob
import os
from typing import Any

import structlog

from services.event_reader import read_events
from services.event_reader import workflow_status as event_workflow_status

log = structlog.get_logger()

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)

WORKFLOWS_DIR = os.path.join(WORKSPACE_ROOT, "workflows")

# Known workflow-generator filenames. pegasus-isi repos are inconsistent: most
# use the underscore form, but quickstart-workflow ships the hyphenated name,
# and some declare a custom name in `.pegasushub.yml` (scripts.generator).
_GENERATOR_NAMES = ("workflow_generator.py", "workflow-generator.py")

# PegasusHub registry marker file present at the root of a workflow repo.
PEGASUSHUB_MARKER = ".pegasushub.yml"


def _pegasushub_generator(project_dir: str) -> str | None:
    """Generator filename declared in a repo's ``.pegasushub.yml``
    (``scripts.generator``), or None. Only a bare filename that exists inside
    the project is honored — no path traversal outside project_dir."""
    marker = os.path.join(project_dir, PEGASUSHUB_MARKER)
    if not os.path.isfile(marker):
        return None
    try:
        import yaml

        with open(marker, encoding="utf-8", errors="replace") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:  # best-effort marker; never break the scan
        log.warning("pegasushub_marker_parse_failed", path=marker, error=str(e))
        return None
    if not isinstance(data, dict):
        return None
    gen = (data.get("scripts") or {}).get("generator")
    if not isinstance(gen, str) or not gen.strip():
        return None
    name = os.path.basename(gen.strip())
    if name and os.path.isfile(os.path.join(project_dir, name)):
        return name
    return None


def find_generator(project_dir: str) -> str | None:
    """Absolute path to a project's workflow generator, or None if absent.

    Resolution order: the name declared in ``.pegasushub.yml`` (if present and
    the file exists), then the known filename variants (underscore, hyphen).
    """
    declared = _pegasushub_generator(project_dir)
    if declared:
        return os.path.join(project_dir, declared)
    for name in _GENERATOR_NAMES:
        path = os.path.join(project_dir, name)
        if os.path.isfile(path):
            return path
    return None


# Root under which discover_runs() looks for Pegasus run directories. Defaults
# to the whole home dir so runs submitted outside $HOME/work are still found.
# Override with STUDIO_SCAN_DIR.
SCAN_ROOT = os.environ.get(
    "STUDIO_SCAN_DIR",
    os.environ.get("HOME", "/tmp"),
)

# Path fragments that mark a braindump.yml as NOT a real user run — e.g. the
# Pegasus source tree's own analyzer/status test fixtures, dependency installs,
# or VCS internals. A run dir whose path contains any of these is skipped so a
# cloned repo (e.g. ~/pegasus) can't pollute the run list with sample data.
_EXCLUDE_RUN_PATH_FRAGMENTS = (
    os.sep + "test" + os.sep,
    os.sep + "tests" + os.sep,
    "analyzer_samples_dir",
    "status_sample_files",
    "site-packages",
    os.sep + ".git" + os.sep,
    os.sep + "node_modules" + os.sep,
)


def _is_excluded_run_dir(run_dir: str) -> bool:
    """True for run dirs that belong to cloned repos / test fixtures."""
    return any(frag in run_dir for frag in _EXCLUDE_RUN_PATH_FRAGMENTS)


def discover_runs(workspace: str | None = None) -> list[dict[str, Any]]:
    """Find all Pegasus run directories under the scan root.

    Looks for braindump.yml files which Pegasus creates in every run directory
    after pegasus-plan. Scans SCAN_ROOT (the home dir by default, overridable
    via STUDIO_SCAN_DIR) and skips paths that belong to cloned repos or test
    fixtures, so unrelated braindumps don't show up as runs.

    Returns list of dicts with keys: run_id, name, run_dir, status.
    """
    workspace = workspace or SCAN_ROOT
    runs: list[dict[str, Any]] = []

    # Pegasus creates braindump.yml in the submit directory
    pattern = os.path.join(workspace, "**", "braindump.yml")
    for braindump_path in glob.glob(pattern, recursive=True):
        run_dir = os.path.dirname(braindump_path)
        if _is_excluded_run_dir(run_dir):
            continue
        try:
            bd = _parse_braindump(braindump_path)
            run_id = bd.get("wf_uuid", os.path.basename(run_dir))
            name = bd.get("pegasus_wf_name", os.path.basename(run_dir))
            runs.append({
                "run_id": run_id,
                # Human-friendly submit-dir name (e.g. "run0001") for display;
                # run_id stays the globally-unique wf_uuid used for routing.
                "run_name": os.path.basename(run_dir),
                "name": name,
                "run_dir": run_dir,
                "submit_dir": bd.get("submit_dir", run_dir),
                "user": bd.get("user", ""),
                "grid_dn": bd.get("grid_dn"),
                "planner_version": bd.get("planner_version"),
                "dax_label": bd.get("dax_label"),
                "timestamp": bd.get("timestamp"),
            })
        except Exception as e:
            log.warning("braindump_parse_failed", path=braindump_path, error=str(e))
            # Still include with basic info
            runs.append({
                "run_id": os.path.basename(run_dir),
                "name": os.path.basename(run_dir),
                "run_dir": run_dir,
            })

    log.info("workflow_scan_complete", count=len(runs), workspace=workspace)
    return runs


def discover_projects(workspace: str | None = None) -> list[dict[str, Any]]:
    """Find workflow projects in the workflows directory.

    Scans ~/work/workflows/ for directories containing workflow_generator.py
    or workflow YAML files.  Returns a list of project dicts that merge
    unsubmitted project metadata with any submitted runs found inside the
    project tree.
    """
    workspace = workspace or WORKFLOWS_DIR
    projects: list[dict[str, Any]] = []

    if not os.path.isdir(workspace):
        log.info("workflows_dir_missing", path=workspace)
        return projects

    for entry in sorted(os.listdir(workspace)):
        project_dir = os.path.join(workspace, entry)
        if not os.path.isdir(project_dir):
            continue

        generator_path = find_generator(project_dir)
        has_generator = generator_path is not None

        # Non-braindump YAML at the top level (a generated workflow file, etc).
        yml_files = glob.glob(os.path.join(project_dir, "*.yml")) + glob.glob(
            os.path.join(project_dir, "*.yaml")
        )
        has_workflow_yml = any(
            os.path.basename(f) not in ("braindump.yml", "braindump.yaml")
            for f in yml_files
        )

        # The PegasusHub marker (a dotfile the globs above skip) also identifies
        # a workflow project — even a registry repo cloned before Generate runs.
        has_pegasushub = os.path.isfile(
            os.path.join(project_dir, PEGASUSHUB_MARKER)
        )

        has_dockerfile = os.path.isfile(
            os.path.join(project_dir, "Docker", "Dockerfile")
        ) or os.path.isfile(os.path.join(project_dir, "Dockerfile"))

        # Skip directories that don't look like workflow projects
        if not has_generator and not has_workflow_yml and not has_pegasushub:
            continue

        # Find submitted runs inside this project (braindump.yml files)
        submitted_runs: list[dict[str, Any]] = []
        for bd_path in glob.glob(
            os.path.join(project_dir, "**", "braindump.yml"), recursive=True
        ):
            run_dir = os.path.dirname(bd_path)
            if _is_excluded_run_dir(run_dir):
                continue
            try:
                bd = _parse_braindump(bd_path)
                run_id = bd.get("wf_uuid", os.path.basename(run_dir))
                run_status = get_run_status(run_dir)
                submitted_runs.append({
                    "run_id": run_id,
                    "run_name": os.path.basename(run_dir),
                    "name": bd.get("pegasus_wf_name", os.path.basename(run_dir)),
                    "run_dir": run_dir,
                    "status": run_status,
                })
            except Exception as exc:
                log.warning("project_run_parse_failed", path=bd_path, error=str(exc))

        # Newest run first (run0011 before run0001).
        submitted_runs.sort(key=lambda r: r.get("run_name", ""), reverse=True)

        # Derive overall project status
        if submitted_runs:
            run_statuses = [r["status"] for r in submitted_runs]
            if "running" in run_statuses:
                status = "running"
            elif "failed" in run_statuses:
                status = "failed"
            elif all(s == "succeeded" for s in run_statuses):
                status = "succeeded"
            else:
                status = "planned"
        elif has_workflow_yml:
            status = "generated"
        else:
            status = "draft"

        projects.append({
            "project_id": entry,
            "name": entry.replace("-", " ").replace("_", " ").title(),
            "project_dir": project_dir,
            "status": status,
            "has_generator": has_generator,
            "generator_file": (
                os.path.basename(generator_path) if generator_path else None
            ),
            "has_workflow_yml": has_workflow_yml,
            "has_dockerfile": has_dockerfile,
            "submitted_runs": submitted_runs,
        })

    log.info("project_scan_complete", count=len(projects), workspace=workspace)
    return projects


def _parse_braindump(path: str) -> dict[str, str]:
    """Parse a braindump.yml file into a dict.

    braindump.yml is a simple YAML file with key: value pairs.
    We parse it manually to avoid requiring PyYAML for simple cases,
    but fall back to yaml.safe_load if available.
    """
    try:
        import yaml

        with open(path) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        pass

    # Fallback: simple key: value parser
    result: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip()
    return result


def _jobstate_terminal_status(run_dir: str) -> str | None:
    """Definitive terminal status from jobstate.log, where monitord records
    ``DAGMAN_FINISHED <code>`` (0 = success, non-zero = failure). Returns
    "succeeded"/"failed" if the DAG finished, "running" if it started but has
    not finished, or None if jobstate.log is absent/inconclusive.

    The last DAGMAN_FINISHED wins (a re-run appends a fresh one). monitord.done
    only means monitord finished parsing — a failed run writes it too — so it is
    NOT used as a success signal.
    """
    jobstate = os.path.join(run_dir, "jobstate.log")
    if not os.path.isfile(jobstate):
        return None
    finished_code: int | None = None
    started = False
    try:
        with open(jobstate, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "DAGMAN_FINISHED" in line:
                    parts = line.split("DAGMAN_FINISHED", 1)[1].split()
                    if parts:
                        try:
                            finished_code = int(parts[0])
                        except ValueError:
                            pass
                elif "DAGMAN_STARTED" in line:
                    started = True
    except OSError:
        return None
    if finished_code is not None:
        return "succeeded" if finished_code == 0 else "failed"
    if started:
        return "running"
    return None


def _pegasus_status(run_dir: str) -> str | None:
    """Authoritative live status via ``pegasus-status``. Parses the DAG STATE
    column (Success/Failure/Running). Returns None if pegasus-status is
    unavailable, times out, or reports no DAG (not yet submitted)."""
    import subprocess

    try:
        proc = subprocess.run(
            ["pegasus-status", "-l", run_dir],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    out = proc.stdout or ""
    # Summary line, e.g. "Summary: 1 DAG total (Success:1)" / "(Failure:1)" /
    # "(Running:1)". Fall back to the per-DAG STATE column word.
    low = out.lower()
    if "failure" in low:
        return "failed"
    if "success" in low:
        return "succeeded"
    if "running" in low:
        return "running"
    return None


def get_run_status(run_dir: str) -> str:
    """Determine workflow status for a run directory.

    Resolution order (most authoritative first):
      1. The workflow event log (workflow-events.jsonl / wfevents.jsonl).
      2. jobstate.log DAGMAN_FINISHED — definitive terminal succeeded/failed.
      3. pegasus-status — authoritative live state for submitted-but-unfinished
         runs (also detects "not submitted").
      4. On-disk shape: planned-but-not-submitted vs in-planning.
    """
    events = read_events(run_dir)
    if events:
        return event_workflow_status(events)

    # Terminal status from jobstate.log is fast and definitive — trust it and
    # skip the (slower, condor-querying) pegasus-status call.
    terminal = _jobstate_terminal_status(run_dir)
    if terminal in ("succeeded", "failed"):
        return terminal

    # Submitted but not terminal (jobstate says running, or a submitted DAG with
    # no jobstate yet): ask pegasus-status for the authoritative live state.
    submitted = terminal == "running" or os.path.isfile(
        os.path.join(run_dir, "monitord.log")
    ) or bool(glob.glob(os.path.join(run_dir, "*.dag.dagman.out")))
    if submitted:
        live = _pegasus_status(run_dir)
        if live:
            return live
        return "running"

    # Not submitted: a planned run has the DAGMan submit file but never ran.
    # Distinguish "planned" (ready to submit, idle) from "planning" (in progress).
    if glob.glob(os.path.join(run_dir, "*.dag.condor.sub")) or glob.glob(
        os.path.join(run_dir, "*.dag")
    ):
        return "planned"
    return "planning"
