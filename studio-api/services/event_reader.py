"""Reader for workflow-monitor JSONL event logs.

The studio drives all workflow status / job / stats displays from the
``workflow-events.jsonl`` file produced by ``workflow-monitor --serve`` instead
of parsing the Pegasus stampede DB directly. This keeps a single, maintained
parser (the workflow-monitor tool) as the source of truth.

Event schema (one JSON object per line), produced by
workflow_monitor.event_log.EventLogger:

  workflow_start  {dax_label, user, planner_version, submit_dir, wf_start}
  jobs_init       {total_jobs, jobs:[{job_id, exec_job_id, type_desc,
                                       transformation?, task_argv?}]}
  workflow_state  {state, status, wf_start?/wf_end?}
  job_state       {job_id, exec_job_id, type_desc, state, exitcode?,
                   stdout_file?, stderr_file?, maxrss?}
  workflow_stats  {stats:{...}}
  workflow_end    {wf_state, wf_status, wf_end, total_jobs, done, failed,
                   elapsed}

Note: per-job ``site`` and ``duration`` are NOT in the JSONL schema, so they
are not surfaced here (they would require an upstream change to the monitor's
event emitter).
"""

from __future__ import annotations

import json
import os
from typing import Any

import structlog

log = structlog.get_logger()

EVENTS_FILENAME = "workflow-events.jsonl"


def find_events_file(run_dir: str) -> str | None:
    """Locate the workflow-events.jsonl for a run dir (or its submit_dir)."""
    direct = os.path.join(run_dir, EVENTS_FILENAME)
    if os.path.isfile(direct):
        return direct

    braindump = os.path.join(run_dir, "braindump.yml")
    if os.path.isfile(braindump):
        try:
            import yaml

            with open(braindump) as f:
                bd = yaml.safe_load(f) or {}
            submit_dir = bd.get("submit_dir")
            if submit_dir:
                alt = os.path.join(submit_dir, EVENTS_FILENAME)
                if os.path.isfile(alt):
                    return alt
        except Exception:
            pass
    return None


def read_events(run_dir: str) -> list[dict[str, Any]]:
    """Read and parse all events from a run's JSONL log (empty if none yet)."""
    path = find_events_file(run_dir)
    if not path:
        return []

    events: list[dict[str, Any]] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        log.warning("events_read_failed", run_dir=run_dir, error=str(e))
        return []
    return events


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _job_status(state: str | None, exitcode: Any) -> str:
    """Map a stampede job state + exitcode to succeeded/failed/running/pending."""
    code = _as_int(exitcode)
    if code is not None:
        return "succeeded" if code == 0 else "failed"
    s = (state or "").upper()
    if "SUCCESS" in s:
        return "succeeded"
    if "FAIL" in s:
        return "failed"
    if s in (
        "SUBMIT", "GRID_SUBMIT", "EXECUTE", "JOB_HELD",
        "PRE_SCRIPT_STARTED", "POST_SCRIPT_STARTED",
    ):
        return "running"
    return "pending"


def workflow_status(events: list[dict[str, Any]]) -> str:
    """Derive succeeded / failed / running / planning from the event stream."""
    if not events:
        return "planning"

    end = None
    last_state = None
    saw_start = False
    for ev in events:
        et = ev.get("event_type")
        if et == "workflow_end":
            end = ev
        elif et == "workflow_state":
            last_state = ev
        elif et in ("workflow_start", "jobs_init"):
            saw_start = True

    if end is not None:
        code = _as_int(end.get("wf_status"))
        if code is not None:
            return "succeeded" if code == 0 else "failed"
        return (
            "succeeded"
            if end.get("wf_state") == "WORKFLOW_TERMINATED"
            else "running"
        )

    if last_state is not None:
        if last_state.get("state") == "WORKFLOW_TERMINATED":
            code = _as_int(last_state.get("status"))
            return "succeeded" if (code is None or code == 0) else "failed"
        return "running"

    return "running" if saw_start else "planning"


def jobs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct the current per-job roster from jobs_init + job_state events."""
    roster: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []

    def _ensure(jid: Any, exec_job_id: str | None) -> dict[str, Any]:
        entry = roster.get(jid)
        if entry is None:
            entry = {
                "job_id": exec_job_id or str(jid),
                "transformation": None,
                "type_desc": None,
                "state": None,
                "exitcode": None,
                "status": "pending",
                "stdout_file": None,
                "stderr_file": None,
                "maxrss": None,
            }
            roster[jid] = entry
            order.append(jid)
        return entry

    for ev in events:
        if ev.get("event_type") != "jobs_init":
            continue
        for j in ev.get("jobs", []):
            jid = j.get("job_id", j.get("exec_job_id"))
            entry = _ensure(jid, j.get("exec_job_id"))
            entry["transformation"] = j.get("transformation")
            entry["type_desc"] = j.get("type_desc")

    for ev in events:
        if ev.get("event_type") != "job_state":
            continue
        jid = ev.get("job_id", ev.get("exec_job_id"))
        entry = _ensure(jid, ev.get("exec_job_id"))
        if ev.get("state"):
            entry["state"] = ev["state"]
        if ev.get("exitcode") is not None:
            entry["exitcode"] = ev["exitcode"]
        if ev.get("stdout_file"):
            entry["stdout_file"] = ev["stdout_file"]
        if ev.get("stderr_file"):
            entry["stderr_file"] = ev["stderr_file"]
        if ev.get("maxrss") is not None:
            entry["maxrss"] = ev["maxrss"]
        if ev.get("transformation"):
            entry["transformation"] = ev["transformation"]
        entry["status"] = _job_status(entry["state"], entry["exitcode"])

    return [roster[k] for k in order]


def workflow_detail(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Workflow-level summary (status + job counts) from the event stream."""
    status = workflow_status(events)

    total = done = failed = 0
    for ev in events:
        et = ev.get("event_type")
        if et == "jobs_init":
            total = ev.get("total_jobs", total) or total
        elif et == "workflow_end":
            total = ev.get("total_jobs", total) or total
            done = ev.get("done", done) or done
            failed = ev.get("failed", failed) or failed

    job_list = jobs(events)
    if not total:
        total = len(job_list)
    if not done and not failed:
        done = sum(1 for j in job_list if j["status"] == "succeeded")
        failed = sum(1 for j in job_list if j["status"] == "failed")
    running = max(0, total - done - failed)

    return {
        "status": status,
        "total_jobs": total,
        "completed_jobs": done,
        "failed_jobs": failed,
        "running_jobs": running,
        "exec_site": None,
    }
