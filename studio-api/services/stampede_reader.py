"""Fallback reader for the Pegasus stampede SQLite DB.

The studio normally drives its job/status/DAG views from a ``workflow-events.jsonl``
log (see :mod:`services.event_reader`). That log only exists if ``workflow-monitor
--serve`` or the monitord ``wfevents`` plugin ran. When neither did, the
authoritative data is still present in ``<submit_dir>/<label>-N.stampede.db`` —
``pegasus-monitord`` writes it for every run.

This module reads that DB (read-only) and synthesizes the **same event list**
``event_reader`` produces, so all downstream consumers (jobs table, DAG colors,
workflow status) work unchanged with no JSONL.
"""

from __future__ import annotations

import glob
import os
import sqlite3
from typing import Any

import structlog

log = structlog.get_logger()

# Max chars of captured stderr to attach to a failed job (keep payloads small).
_STDERR_LIMIT = 4000


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _braindump(run_dir: str) -> dict[str, Any]:
    """The run directory's braindump.yml, or {} if absent or unreadable."""
    braindump = os.path.join(run_dir, "braindump.yml")
    if not os.path.isfile(braindump):
        return {}
    try:
        import yaml

        with open(braindump) as f:
            bd = yaml.safe_load(f)
        return bd if isinstance(bd, dict) else {}
    except Exception:
        return {}


def _candidate_dirs(run_dir: str) -> list[str]:
    """run_dir plus the braindump's submit_dir (where the DB actually lives)."""
    dirs = [run_dir]
    submit_dir = _braindump(run_dir).get("submit_dir")
    if submit_dir and submit_dir != run_dir:
        dirs.append(submit_dir)
    return dirs


def _root_run_dir(run_dir: str, bd: dict[str, Any]) -> str | None:
    """For a sub-workflow's directory, the top-level workflow's directory.

    A hierarchical run has one stampede DB, in the root's submit directory;
    the sub-workflow's own directory (nested under the root's) has only a
    braindump naming the root in ``root_wf_uuid``. Walk up to the braindump
    whose ``wf_uuid`` is that id, the way pegasus-monitor's locator does.
    None for a top-level workflow or when no such directory is above this one.
    """
    root_uuid = bd.get("root_wf_uuid")
    if not root_uuid or root_uuid == bd.get("wf_uuid"):
        return None
    d = os.path.dirname(os.path.abspath(run_dir))
    while True:
        if str(_braindump(d).get("wf_uuid") or "") == str(root_uuid):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _glob_db(directory: str) -> str | None:
    matches = sorted(glob.glob(os.path.join(directory, "*.stampede.db")))
    return matches[0] if matches else None


def _locate(run_dir: str) -> tuple[str, str | None, bool] | None:
    """(db path, this run's wf_uuid, whether the DB is the root workflow's).

    The third value matters to :func:`workflow_id`: in a DB borrowed from the
    root, a sub-workflow monitord has not recorded yet must read as "nothing
    here", not as the root.
    """
    bd = _braindump(run_dir)
    wf_uuid = str(bd["wf_uuid"]) if bd.get("wf_uuid") else None
    for d in _candidate_dirs(run_dir):
        db = _glob_db(d)
        if db:
            return db, wf_uuid, False
    root = _root_run_dir(run_dir, bd)
    if root:
        for d in _candidate_dirs(root):
            db = _glob_db(d)
            if db:
                return db, wf_uuid, True
    return None


def find_stampede_db(run_dir: str) -> str | None:
    """Locate the *.stampede.db for a run dir (skips *.replicas.db).

    For a sub-workflow's directory this is the root workflow's DB, which
    holds every workflow of the hierarchy: read it through
    :func:`open_stampede`, or the rows are the whole hierarchy's.
    """
    found = _locate(run_dir)
    return found[0] if found else None


def workflow_id(
    con: sqlite3.Connection, wf_uuid: str | None, borrowed: bool = False
) -> Any | None:
    """The ``wf_id`` of the one workflow a run directory represents.

    Root and sub-workflows share a stampede DB, and every table hangs off
    ``wf_id``, so a query without it mixes them: the root took the
    sub-workflow's WORKFLOW_TERMINATED for its own and listed its jobs.
    The run's ``wf_uuid`` (braindump.yml, also Studio's run id) names the
    row. A DB with no such row falls back to its top-level workflow, which
    is what a single-workflow DB holds anyway, unless the DB was borrowed
    from the root, where that fallback would show the root under the
    sub-workflow's name. None when there is no workflow to read.
    """
    try:
        if wf_uuid:
            row = con.execute(
                "SELECT wf_id FROM workflow WHERE wf_uuid = ?", (wf_uuid,)
            ).fetchone()
            if row is not None:
                return row[0]
        if borrowed:
            return None
        row = con.execute(
            "SELECT wf_id FROM workflow "
            "ORDER BY (parent_wf_id IS NOT NULL), wf_id LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row is not None else None


def open_stampede(run_dir: str) -> tuple[sqlite3.Connection, Any | None] | None:
    """Open the run's stampede DB read-only: (connection, wf_id), or None.

    ``wf_id`` is None when the DB has no ``workflow`` table (hand-built
    databases) or no row in it yet; a caller then has nothing to scope by. A
    sub-workflow that is not in its root's DB yet yields None outright.
    """
    found = _locate(run_dir)
    if not found:
        return None
    db_path, wf_uuid, borrowed = found
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    except sqlite3.Error as e:
        log.warning("stampede_open_failed", run_dir=run_dir, error=str(e))
        return None
    wf_id = workflow_id(con, wf_uuid, borrowed)
    if wf_id is None and borrowed:
        con.close()
        return None
    return con, wf_id


# Rows of each table that belong to one workflow. job_instance, jobstate,
# invocation and task are reached through `job`, the one table whose wf_id
# every stampede DB Studio has seen carries.
_JOBS_OF_WF = "SELECT job_id FROM job WHERE wf_id = :wf"
_INSTANCES_OF_WF = (
    "SELECT job_instance_id FROM job_instance "
    f"WHERE job_id IN ({_JOBS_OF_WF})"
)


def read_stampede_events(run_dir: str) -> list[dict[str, Any]]:
    """Synthesize event_reader-shaped events from the stampede DB.

    Returns [] if no DB is found or it can't be read. The emitted events use the
    same ``event_type`` vocabulary as the JSONL log: workflow_start, jobs_init,
    job_state, workflow_end / workflow_state.
    """
    opened = open_stampede(run_dir)
    if opened is None:
        return []
    con, wf_id = opened
    scope = {"wf": wf_id}

    try:
        # wf_id None is a DB monitord has created and not yet written a
        # workflow into: every query below then matches nothing, and the
        # bare workflow_start that results reads "running" with no jobs, as
        # it did before the queries were scoped.
        wf = con.execute(
            "SELECT dax_label, submit_dir, user, planner_version, timestamp "
            "FROM workflow WHERE wf_id = :wf",
            scope,
        ).fetchone()

        job_rows = con.execute(
            "SELECT job_id, exec_job_id, type_desc FROM job "
            "WHERE wf_id = :wf ORDER BY job_id",
            scope,
        ).fetchall()

        # job_id -> transformation (from the abstract task)
        task_tx: dict[Any, str] = {}
        for r in con.execute(
            f"SELECT job_id, transformation FROM task WHERE job_id IN ({_JOBS_OF_WF})",
            scope,
        ):
            task_tx.setdefault(r["job_id"], r["transformation"])

        # job_id -> latest job_instance (highest job_submit_seq wins on retries)
        inst_by_job: dict[Any, sqlite3.Row] = {}
        for r in con.execute(
            "SELECT job_id, job_instance_id, site, exitcode, stdout_file, "
            "stderr_file, stderr_text, local_duration, cluster_duration "
            f"FROM job_instance WHERE job_id IN ({_JOBS_OF_WF}) "
            "ORDER BY job_submit_seq",
            scope,
        ):
            inst_by_job[r["job_id"]] = r

        # job_instance_id -> latest state
        state_by_inst: dict[Any, str] = {}
        for r in con.execute(
            "SELECT job_instance_id, state FROM jobstate "
            f"WHERE job_instance_id IN ({_INSTANCES_OF_WF}) "
            "ORDER BY jobstate_submit_seq",
            scope,
        ):
            state_by_inst[r["job_instance_id"]] = r["state"]

        # job_instance_id -> invocation (maxrss / exitcode / transformation)
        inv_by_inst: dict[Any, sqlite3.Row] = {}
        for r in con.execute(
            "SELECT job_instance_id, exitcode, maxrss, transformation, "
            "remote_duration FROM invocation "
            f"WHERE job_instance_id IN ({_INSTANCES_OF_WF})",
            scope,
        ):
            inv_by_inst.setdefault(r["job_instance_id"], r)

        # rowid breaks a tie between a TERMINATED and the restart's STARTED
        # written in the same second: insertion order is the order they
        # happened in.
        wfstates = con.execute(
            "SELECT state, status, timestamp FROM workflowstate "
            "WHERE wf_id = :wf ORDER BY timestamp, rowid",
            scope,
        ).fetchall()
    except sqlite3.Error as e:
        log.warning("stampede_read_failed", run_dir=run_dir, error=str(e))
        return []
    finally:
        con.close()

    events: list[dict[str, Any]] = [{
        "event_type": "workflow_start",
        "dax_label": wf["dax_label"] if wf else None,
        "user": wf["user"] if wf else None,
        "planner_version": wf["planner_version"] if wf else None,
        "submit_dir": (wf["submit_dir"] if wf else None) or run_dir,
        "wf_start": wf["timestamp"] if wf else None,
    }]

    events.append({
        "event_type": "jobs_init",
        "total_jobs": len(job_rows),
        "jobs": [
            {
                "job_id": jr["job_id"],
                "exec_job_id": jr["exec_job_id"],
                "type_desc": jr["type_desc"],
                "transformation": task_tx.get(jr["job_id"]),
            }
            for jr in job_rows
        ],
    })

    for jr in job_rows:
        inst = inst_by_job.get(jr["job_id"])
        if inst is None:
            # Job never produced an instance (e.g. workflow failed before it
            # ran) — leave it out so it stays "pending" / gray, which is correct.
            continue
        jiid = inst["job_instance_id"]
        inv = inv_by_inst.get(jiid)
        # The job's own exit code only. An invocation's is one task's (or the
        # pre-script's: a sub-workflow job has a `dagman::pre` invocation,
        # exit 0, from the moment pegasus-plan finishes), so falling back to
        # it called a sub-workflow "succeeded" while it was still queued.
        exitcode = inst["exitcode"]

        # Wall-clock duration: prefer the job's local_duration, fall back to
        # cluster_duration, then the task's remote_duration.
        duration = inst["local_duration"] or inst["cluster_duration"]
        if duration is None and inv is not None:
            duration = inv["remote_duration"]
        if isinstance(duration, (int, float)):
            duration = round(float(duration), 1)

        # Captured stderr — only attach for failed jobs, truncated, so the
        # failure reason is visible inline without running the analyzer.
        stderr = None
        if _as_int(exitcode) not in (None, 0):
            text = inst["stderr_text"]
            if text:
                stderr = text.strip()[-_STDERR_LIMIT:]

        events.append({
            "event_type": "job_state",
            "job_id": jr["job_id"],
            "exec_job_id": jr["exec_job_id"],
            "type_desc": jr["type_desc"],
            "state": state_by_inst.get(jiid),
            "exitcode": exitcode,
            "stdout_file": inst["stdout_file"],
            "stderr_file": inst["stderr_file"],
            "maxrss": inv["maxrss"] if inv else None,
            "site": inst["site"],
            "duration": duration,
            "stderr": stderr,
            "transformation": task_tx.get(jr["job_id"])
            or (inv["transformation"] if inv else None),
        })

    # The LAST state decides. monitord appends a row per DAGMan session, so a
    # run restarted by the schedd, or re-run from a rescue DAG, reads
    # STARTED, TERMINATED 2, STARTED, TERMINATED 0: taking the first
    # TERMINATED left it "failed" for good, live and after it succeeded. A
    # STARTED after a TERMINATED is a run that is going again. If that
    # session then dies without a trace the DB says "running" forever, which
    # is the zombie case workflow_scanner.dagman_exit_status() exists for.
    terminated = (
        wfstates[-1]
        if wfstates and wfstates[-1]["state"] == "WORKFLOW_TERMINATED"
        else None
    )
    if terminated is not None:
        # done/failed are intentionally omitted so workflow_detail() derives
        # them from the per-job states with the same logic the JSONL path uses.
        events.append({
            "event_type": "workflow_end",
            "wf_state": "WORKFLOW_TERMINATED",
            "wf_status": terminated["status"],
            "total_jobs": len(job_rows),
            "wf_end": terminated["timestamp"],
        })
    elif wfstates:
        events.append({
            "event_type": "workflow_state",
            "state": wfstates[-1]["state"],
            "status": wfstates[-1]["status"],
        })

    return events


def _hosts(con: sqlite3.Connection) -> dict[Any, str]:
    """job_instance_id -> the hostname the job last ran on (#556).

    monitord fills `host` from each job's kickstart record, so this is the
    machine the step actually executed on: a Studio pool EP, an annex node
    on the user's cluster, an OSPool slot, or the access point itself for
    Pegasus's own local jobs. Read separately because older or hand-built
    databases (the tests' among them) have no host table, and missing hosts
    must not cost the rest of the details.
    """
    try:
        return {
            r["job_instance_id"]: r["hostname"]
            for r in con.execute(
                "SELECT ji.job_instance_id, h.hostname FROM job_instance ji "
                "JOIN host h ON ji.host_id = h.host_id "
                "WHERE h.hostname IS NOT NULL"
            )
        }
    except sqlite3.Error:
        return {}


def read_stampede_job_details(run_dir: str) -> dict[str, dict[str, Any]]:
    """Per-job extras keyed by exec_job_id, read straight from the stampede DB.

    Used to enrich JSONL-sourced jobs: the integrated monitord's job_state
    events omit site/duration/stderr (and per-job transformation), but the
    stampede DB always has them. Returns {} if there's no DB.
    """
    opened = open_stampede(run_dir)
    if opened is None:
        return {}
    con, wf_id = opened
    scope = {"wf": wf_id}

    out: dict[str, dict[str, Any]] = {}
    try:
        task_tx: dict[Any, str] = {}
        for r in con.execute(
            f"SELECT job_id, transformation FROM task WHERE job_id IN ({_JOBS_OF_WF})",
            scope,
        ):
            task_tx.setdefault(r["job_id"], r["transformation"])
        inst_by_job: dict[Any, sqlite3.Row] = {}
        for r in con.execute(
            "SELECT job_id, job_instance_id, site, exitcode, stderr_text, "
            "local_duration, cluster_duration FROM job_instance "
            f"WHERE job_id IN ({_JOBS_OF_WF}) "
            "ORDER BY job_submit_seq",
            scope,
        ):
            inst_by_job[r["job_id"]] = r
        inv_by_inst: dict[Any, sqlite3.Row] = {}
        for r in con.execute(
            "SELECT job_instance_id, maxrss, exitcode, transformation, "
            "remote_duration FROM invocation "
            f"WHERE job_instance_id IN ({_INSTANCES_OF_WF})",
            scope,
        ):
            inv_by_inst.setdefault(r["job_instance_id"], r)
        host_by_inst = _hosts(con)

        for jr in con.execute(
            "SELECT job_id, exec_job_id FROM job WHERE wf_id = :wf", scope
        ):
            detail: dict[str, Any] = {
                "transformation": task_tx.get(jr["job_id"]),
                "site": None,
                "duration": None,
                "stderr": None,
                "maxrss": None,
                "host": None,
            }
            inst = inst_by_job.get(jr["job_id"])
            if inst is not None:
                inv = inv_by_inst.get(inst["job_instance_id"])
                exitcode = inst["exitcode"]
                duration = inst["local_duration"] or inst["cluster_duration"]
                if duration is None and inv is not None:
                    duration = inv["remote_duration"]
                if isinstance(duration, (int, float)):
                    duration = round(float(duration), 1)
                stderr = None
                if _as_int(exitcode) not in (None, 0) and inst["stderr_text"]:
                    stderr = inst["stderr_text"].strip()[-_STDERR_LIMIT:]
                detail.update({
                    "site": inst["site"],
                    "duration": duration,
                    "stderr": stderr,
                    "maxrss": inv["maxrss"] if inv else None,
                    "host": host_by_inst.get(inst["job_instance_id"]),
                    "transformation": task_tx.get(jr["job_id"])
                    or (inv["transformation"] if inv else None),
                })
            out[jr["exec_job_id"]] = detail
    except sqlite3.Error as e:
        log.warning("stampede_read_failed", run_dir=run_dir, error=str(e))
        return {}
    finally:
        con.close()

    return out
