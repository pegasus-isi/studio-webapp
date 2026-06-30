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


def _candidate_dirs(run_dir: str) -> list[str]:
    """run_dir plus the braindump's submit_dir (where the DB actually lives)."""
    dirs = [run_dir]
    braindump = os.path.join(run_dir, "braindump.yml")
    if os.path.isfile(braindump):
        try:
            import yaml

            with open(braindump) as f:
                bd = yaml.safe_load(f) or {}
            submit_dir = bd.get("submit_dir")
            if submit_dir and submit_dir != run_dir:
                dirs.append(submit_dir)
        except Exception:
            pass
    return dirs


def find_stampede_db(run_dir: str) -> str | None:
    """Locate the *.stampede.db for a run dir (skips *.replicas.db)."""
    for d in _candidate_dirs(run_dir):
        matches = sorted(glob.glob(os.path.join(d, "*.stampede.db")))
        if matches:
            return matches[0]
    return None


def read_stampede_events(run_dir: str) -> list[dict[str, Any]]:
    """Synthesize event_reader-shaped events from the stampede DB.

    Returns [] if no DB is found or it can't be read. The emitted events use the
    same ``event_type`` vocabulary as the JSONL log: workflow_start, jobs_init,
    job_state, workflow_end / workflow_state.
    """
    db_path = find_stampede_db(run_dir)
    if not db_path:
        return []

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    except sqlite3.Error as e:
        log.warning("stampede_open_failed", run_dir=run_dir, error=str(e))
        return []

    try:
        wf = con.execute(
            "SELECT dax_label, submit_dir, user, planner_version, timestamp "
            "FROM workflow ORDER BY (parent_wf_id IS NOT NULL), wf_id LIMIT 1"
        ).fetchone()

        job_rows = con.execute(
            "SELECT job_id, exec_job_id, type_desc FROM job ORDER BY job_id"
        ).fetchall()

        # job_id -> transformation (from the abstract task)
        task_tx: dict[Any, str] = {}
        for r in con.execute("SELECT job_id, transformation FROM task"):
            task_tx.setdefault(r["job_id"], r["transformation"])

        # job_id -> latest job_instance (highest job_submit_seq wins on retries)
        inst_by_job: dict[Any, sqlite3.Row] = {}
        for r in con.execute(
            "SELECT job_id, job_instance_id, site, exitcode, stdout_file, "
            "stderr_file FROM job_instance ORDER BY job_submit_seq"
        ):
            inst_by_job[r["job_id"]] = r

        # job_instance_id -> latest state
        state_by_inst: dict[Any, str] = {}
        for r in con.execute(
            "SELECT job_instance_id, state FROM jobstate ORDER BY jobstate_submit_seq"
        ):
            state_by_inst[r["job_instance_id"]] = r["state"]

        # job_instance_id -> invocation (maxrss / exitcode / transformation)
        inv_by_inst: dict[Any, sqlite3.Row] = {}
        for r in con.execute(
            "SELECT job_instance_id, exitcode, maxrss, transformation FROM invocation"
        ):
            inv_by_inst.setdefault(r["job_instance_id"], r)

        wfstates = con.execute(
            "SELECT state, status, timestamp FROM workflowstate ORDER BY timestamp"
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
        exitcode = inst["exitcode"]
        if exitcode is None and inv is not None:
            exitcode = inv["exitcode"]
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
            "transformation": task_tx.get(jr["job_id"])
            or (inv["transformation"] if inv else None),
        })

    terminated = next(
        (w for w in wfstates if w["state"] == "WORKFLOW_TERMINATED"), None
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
