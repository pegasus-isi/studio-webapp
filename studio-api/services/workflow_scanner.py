"""Workflow scanner — discovers Pegasus run directories in the workspace.

Scans for braindump.yml files and extracts workflow metadata.
"""

from __future__ import annotations

import asyncio
import glob
import os
import re
from datetime import datetime
from typing import Any

import structlog

from services.event_reader import read_events
from services.event_reader import workflow_status as event_workflow_status
from workspace import legacy_workflows_dirs, workflows_dir, workspace_root

log = structlog.get_logger()

WORKSPACE_ROOT = workspace_root()

WORKFLOWS_DIR = workflows_dir(WORKSPACE_ROOT)


def project_roots() -> list[str]:
    """Every directory workflow projects are listed from, current one first.

    The second is the pre-#401 ``$HOME/work/workflows``. Those projects are on
    persistent NFS homes and are not moved or rewritten — listing them is the
    whole migration, so a user who had projects before the workspace collapsed
    into $HOME opens Studio and finds them where they left them.

    Read from the module constant rather than resolved afresh so a test (and
    the marketplace router) can still repoint WORKFLOWS_DIR.
    """
    return [WORKFLOWS_DIR, *legacy_workflows_dirs(WORKSPACE_ROOT)]

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


def _is_excluded_run_dir(run_dir: str, extra: tuple | list = ()) -> bool:
    """True for run dirs that belong to cloned repos / test fixtures, or match
    a user-added exclude fragment (plain substrings, same semantics as the
    builtin list)."""
    if any(frag in run_dir for frag in _EXCLUDE_RUN_PATH_FRAGMENTS):
        return True
    return any(frag and frag in run_dir for frag in extra)


def _effective_scan_roots(
    primary: str, extra_roots: tuple | list = ()
) -> list[str]:
    """The primary scan root plus user-added ones, deduplicated.

    An extra root nested under an already-scanned root is dropped — the
    recursive glob would visit it anyway and every run inside would list
    twice. Missing directories are skipped silently: a root that points at an
    unmounted volume should mean "no runs from there right now", not a broken
    Workflows page.
    """
    roots: list[str] = []
    for root in [primary, *extra_roots]:
        if not root or not os.path.isdir(root):
            continue
        real = os.path.realpath(root)
        if any(
            real == kept or real.startswith(kept + os.sep) for kept in roots
        ):
            continue
        roots.append(real)
    return roots


def discover_runs(
    workspace: str | None = None,
    extra_roots: tuple | list = (),
    extra_excludes: tuple | list = (),
) -> list[dict[str, Any]]:
    """Find all Pegasus run directories under the scan roots.

    Looks for braindump.yml files which Pegasus creates in every run directory
    after pegasus-plan. Scans SCAN_ROOT (the home dir by default, overridable
    via STUDIO_SCAN_DIR) plus any user-configured extra roots, and skips paths
    that belong to cloned repos or test fixtures — or match a user-added
    exclude fragment — so unrelated braindumps don't show up as runs.

    The async wrapper resolves ``extra_roots``/``extra_excludes`` from the
    user's settings (see routers/settings.py, workflow-scan); direct callers
    can pass them explicitly.

    Returns list of dicts with keys: run_id, name, run_dir, status.
    """
    runs: list[dict[str, Any]] = []
    seen_run_dirs: set[str] = set()

    for root in _effective_scan_roots(workspace or SCAN_ROOT, extra_roots):
        # Pegasus creates braindump.yml in the submit directory
        pattern = os.path.join(root, "**", "braindump.yml")
        for braindump_path in glob.glob(pattern, recursive=True):
            run_dir = os.path.dirname(braindump_path)
            if run_dir in seen_run_dirs:
                continue
            seen_run_dirs.add(run_dir)
            if _is_excluded_run_dir(run_dir, extra_excludes):
                continue
            try:
                bd = _parse_braindump(braindump_path)
                run_id = bd.get("wf_uuid", os.path.basename(run_dir))
                name = bd.get("pegasus_wf_name", os.path.basename(run_dir))
                runs.append({
                    "run_id": run_id,
                    # Human-friendly submit-dir name (e.g. "run0001") for
                    # display; run_id stays the globally-unique wf_uuid used
                    # for routing.
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
                log.warning(
                    "braindump_parse_failed", path=braindump_path, error=str(e)
                )
                # Still include with basic info
                runs.append({
                    "run_id": os.path.basename(run_dir),
                    "name": os.path.basename(run_dir),
                    "run_dir": run_dir,
                })

    log.info("workflow_scan_complete", count=len(runs), workspace=workspace)
    return runs


def submit_base(project_dir: str, run_dir: str) -> str:
    """The submit base a run was planned into, relative to its project.

    Pegasus lays a run out as ``<base>/<user>/pegasus/<wf-name>/runNNNN``,
    where ``<base>`` is pegasus-plan's ``--dir`` (the cwd when omitted). So the
    base is the run path relative to the project with those four trailing
    components removed: ``""`` for a run planned from the project root,
    ``"submit"`` for ``--dir submit``. A path too short to carry the layout
    (a hand-made fixture) reads as the root.
    """
    rel = os.path.relpath(run_dir, project_dir)
    parts = [c for c in rel.split(os.sep) if c and c != "."]
    if len(parts) <= 4:
        return ""
    return "/".join(parts[:-4])


def _planned_at(bd: dict[str, Any], braindump_path: str) -> float:
    """When a run was planned, as an epoch — the braindump's own timestamp
    (``20261007T154757+0000``), else the file's mtime."""
    ts = bd.get("timestamp")
    if ts:
        for fmt in ("%Y%m%dT%H%M%S%z", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(str(ts), fmt).timestamp()
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(str(ts)).timestamp()
        except ValueError:
            pass
    try:
        return os.path.getmtime(braindump_path)
    except OSError:
        return 0.0


#: Run states that say nothing about how the workflow last went: planned but
#: never submitted, or a status read that failed.
_NOT_AN_OUTCOME = frozenset({"planned", "planning", "unknown", "", None})


def project_status(runs: list[dict[str, Any]]) -> str:
    """A project's status from its runs, which arrive newest-planned first.

    Any run in flight wins. Otherwise it is the newest *submitted* run's
    outcome. This used to be "failed if any run ever failed", so a project
    whose latest run succeeded stayed red on Analyses and on the home page for
    as long as one old failure sat in its submit tree (streamflow: run0002
    failed, the re-run after the fix succeeded, the badge never cleared). A
    planned-but-unsubmitted run is skipped: it says nothing about how the
    workflow last went.
    """
    statuses = [r.get("status") for r in runs]
    if "running" in statuses:
        return "running"
    for status in statuses:
        if status not in _NOT_AN_OUTCOME:
            return str(status)
    return "planned"


def label_runs(project_dir: str, runs: list[dict[str, Any]]) -> None:
    """Name each run so two from different submit bases cannot read alike.

    Runs are shown by their directory's basename, ``run0002``. A project that
    was planned from the GUI (project root) and from a terminal (``--dir
    submit``) holds two run0002s, and nothing in the list told them apart.
    When more than one base is present, a run outside the root is labelled
    with its base, ``submit/run0002``; a project with one base keeps the short
    names. Also orders newest-planned first: with two bases, name order
    interleaves them wrongly.
    """
    for r in runs:
        r["submit_base"] = submit_base(project_dir, r["run_dir"])
    if len({r["submit_base"] for r in runs}) > 1:
        for r in runs:
            if r["submit_base"]:
                r["run_name"] = f'{r["submit_base"]}/{os.path.basename(r["run_dir"])}'
    runs.sort(key=lambda r: (r.get("planned_at", 0.0), r.get("run_name", "")), reverse=True)


def discover_projects(
    workspace: str | None = None,
    extra_excludes: tuple | list = (),
) -> list[dict[str, Any]]:
    """Find workflow projects.

    Scans $HOME/workflows/ for directories containing workflow_generator.py or
    workflow YAML files, then the legacy $HOME/work/workflows/ for anything a
    user left there before the workspace moved. Returns a list of project dicts
    that merge unsubmitted project metadata with any submitted runs found
    inside the project tree. ``extra_excludes`` extends the builtin run-dir
    exclusion fragments (the projects dir itself is not affected by scan
    roots).

    An explicit ``workspace`` scans that one directory and nothing else.
    """
    roots = [workspace] if workspace else project_roots()

    projects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        for project in _scan_projects_dir(root, extra_excludes):
            if project["project_id"] in seen:
                # Same directory name in both roots. The current workspace
                # wins; naming the loser matters, because the one the user is
                # looking at is not the one they just edited.
                log.info(
                    "project_shadowed",
                    project_id=project["project_id"],
                    ignored=project["project_dir"],
                )
                continue
            seen.add(project["project_id"])
            projects.append(project)

    log.info("project_scan_complete", count=len(projects), roots=roots)
    return projects


def _scan_projects_dir(
    workspace: str,
    extra_excludes: tuple | list = (),
) -> list[dict[str, Any]]:
    """Workflow projects directly inside one directory."""
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

        # An Apptainer definition file is what "Build Image" consumes; the
        # workflow then references the resulting .sif by absolute path.
        has_container_def = bool(
            glob.glob(os.path.join(project_dir, "Apptainer", "*.def"))
            or glob.glob(os.path.join(project_dir, "*.def"))
        )

        # Skip directories that don't look like workflow projects
        if not has_generator and not has_workflow_yml and not has_pegasushub:
            continue

        # Find submitted runs inside this project (braindump.yml files)
        submitted_runs: list[dict[str, Any]] = []
        for bd_path in glob.glob(
            os.path.join(project_dir, "**", "braindump.yml"), recursive=True
        ):
            run_dir = os.path.dirname(bd_path)
            if _is_excluded_run_dir(run_dir, extra_excludes):
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
                    "planned_at": _planned_at(bd, bd_path),
                })
            except Exception as exc:
                log.warning("project_run_parse_failed", path=bd_path, error=str(exc))

        # Newest-planned first, and bases told apart when there are two.
        label_runs(project_dir, submitted_runs)

        # Derive overall project status
        if submitted_runs:
            status = project_status(submitted_runs)
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
            "has_container_def": has_container_def,
            "submitted_runs": submitted_runs,
        })

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


# How much of dagman.out's tail to scan for the session-exit marker. DAGMan
# writes EXITING WITH STATUS as effectively its last line, so a few KB is
# plenty; 64KB leaves room for post-exit noise without reading a whole log.
_DAGMAN_OUT_TAIL_BYTES = 64 * 1024

# Session markers in *.dag.dagman.out. Every DAGMan session — normal end,
# abort, OOM kill cleanup — closes with the EXITING line; a rescue/re-run
# appends a fresh session that opens with the STARTING UP banner.
_DAGMAN_EXITING_RE = re.compile(r"EXITING WITH STATUS\s+(-?\d+)")
_DAGMAN_STARTUP_MARKER = "STARTING UP"


def dagman_exit_status(run_dir: str) -> str | None:
    """Terminal status from the DAGMan session log, or None while DAGMan is
    (as far as its log shows) still alive.

    When DAGMan dies abnormally it takes monitord down with it, so no terminal
    marker is ever written: the event stream ends mid-run, jobstate.log has no
    DAGMAN_FINISHED, and the stampede DB holds a lone WORKFLOW_STARTED row.
    Every status source derived from those reads "running" forever (#296).
    dagman.out is the one file DAGMan itself always closes out — every session
    ends with ``**** condor_scheduniv_exec.<id> (condor_DAGMAN) pid <pid>
    EXITING WITH STATUS <n>``, abort or not.

    Reads only the file tail: discover_projects() calls get_run_status() per
    run per listing request, so this must stay a cheap disk read, never a
    condor-querying subprocess. The *last* EXITING line wins, and only when no
    STARTING UP banner follows it — a later banner means a rescue/re-run is
    live in the same file.
    """
    candidates = glob.glob(os.path.join(run_dir, "*.dag.dagman.out"))
    if not candidates:
        return None
    # A rescue DAG can leave several dagman.out files; the newest is the one
    # describing the current/last session.
    path = max(candidates, key=lambda p: os.path.getmtime(p))
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > _DAGMAN_OUT_TAIL_BYTES:
                f.seek(size - _DAGMAN_OUT_TAIL_BYTES)
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None

    exit_code: int | None = None
    for line in tail.splitlines():
        m = _DAGMAN_EXITING_RE.search(line)
        if m:
            try:
                exit_code = int(m.group(1))
            except ValueError:
                continue
        elif _DAGMAN_STARTUP_MARKER in line and exit_code is not None:
            # A session started after the last recorded exit — live again.
            exit_code = None

    if exit_code is None:
        return None
    # A clean exit (0) normally also wrote workflow_end/DAGMAN_FINISHED, so in
    # practice this path converts zombies to "failed"; handle 0 anyway.
    return "succeeded" if exit_code == 0 else "failed"


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
      1. The run's events: the stampede DB, or a legacy JSONL event log
         when the run directory holds one (services/event_reader.py).
      2. jobstate.log DAGMAN_FINISHED — definitive terminal succeeded/failed.
      3. pegasus-status — authoritative live state for submitted-but-unfinished
         runs (also detects "not submitted").
      4. On-disk shape: planned-but-not-submitted vs in-planning.
    """
    events = read_events(run_dir)
    if events:
        status = event_workflow_status(events)
        if status == "running":
            # The event stream is truthful ("no end event seen") but not
            # sufficient: an abnormal DAGMan death takes monitord with it, so
            # no end event is ever written and the run reads "running"
            # forever (#296). Cross-check that DAGMan is actually alive.
            terminal = dagman_exit_status(run_dir)
            if terminal:
                return terminal
        return status

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


# --- async wrappers (BE-1) -------------------------------------------------
# The three functions above do blocking work: recursive globs over NFS, YAML
# parsing, stampede-DB reads, and (get_run_status) a subprocess with a 15s
# timeout. They must NOT run on the event loop — a single call would freeze
# every other request/stream/WS in the container for up to 15s x N runs. Async
# handlers and the notification loop call these wrappers, which run the sync
# body in the default thread-pool executor (the pattern dashboard.py used).


async def _user_scan_config() -> tuple[list[str], list[str]]:
    """User-configured extra scan roots and exclude fragments from settings
    (written by routers/settings.py's workflow-scan endpoints).

    Soft-failing by design: settings being unreadable (no DB in a bare unit
    test, a corrupt row) must degrade to the env-default scan, never break
    discovery — an empty Workflows page with no cause is the failure mode
    this avoids. Validation happens at the PUT endpoint; this trusts shape
    only as far as "list of strings"."""
    try:
        from main import db

        roots = await db.get_setting("workflow_scan_roots", []) or []
        excludes = await db.get_setting("workflow_scan_excludes", []) or []
        return (
            [r for r in roots if isinstance(r, str) and os.path.isabs(r)],
            [e for e in excludes if isinstance(e, str) and e.strip()],
        )
    except Exception:  # noqa: BLE001 — discovery must survive settings issues
        return [], []


async def discover_runs_async(workspace: str | None = None) -> list[dict[str, Any]]:
    extra_roots, extra_excludes = await _user_scan_config()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: discover_runs(
            workspace, extra_roots=extra_roots, extra_excludes=extra_excludes
        ),
    )


async def discover_projects_async(
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    _, extra_excludes = await _user_scan_config()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: discover_projects(workspace, extra_excludes=extra_excludes),
    )


async def get_run_status_async(run_dir: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_run_status, run_dir)
