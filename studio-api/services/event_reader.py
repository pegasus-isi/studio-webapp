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

# Event-log filenames, in priority order. Both share the same schema:
#   workflow-events.jsonl — from `workflow-monitor --serve`
#   wfevents.jsonl        — from the pegasus-monitord `wfevents` plugin (emitted
#                           automatically for every run; see studio-system VM)
EVENTS_FILENAMES = ("workflow-events.jsonl", "wfevents.jsonl")
EVENTS_FILENAME = EVENTS_FILENAMES[0]


def find_events_file(run_dir: str) -> str | None:
    """Locate a workflow event log for a run dir (or its submit_dir).

    Prefers workflow-events.jsonl, then the wfevents plugin's wfevents.jsonl.
    """
    candidate_dirs = [run_dir]
    braindump = os.path.join(run_dir, "braindump.yml")
    if os.path.isfile(braindump):
        try:
            import yaml

            with open(braindump) as f:
                bd = yaml.safe_load(f) or {}
            submit_dir = bd.get("submit_dir")
            if submit_dir and submit_dir != run_dir:
                candidate_dirs.append(submit_dir)
        except Exception:
            pass

    for d in candidate_dirs:
        for fname in EVENTS_FILENAMES:
            path = os.path.join(d, fname)
            if os.path.isfile(path):
                return path
    return None


def read_events(run_dir: str) -> list[dict[str, Any]]:
    """Read and parse all events for a run.

    Prefers the JSONL log (workflow-monitor / wfevents plugin). When no JSONL
    exists, falls back to synthesizing events from the Pegasus stampede DB,
    which monitord writes for every run — so jobs/status/DAG still populate
    even if the monitor never ran.
    """
    path = find_events_file(run_dir)
    if not path:
        from services.stampede_reader import read_stampede_events

        return read_stampede_events(run_dir)

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


# Stampede job states during which the job is being worked on: on a worker, or
# in its pre/post-script on the access point. The *_TERMINATED states are the
# moment between one of those ending and its verdict being recorded.
_EXECUTING = frozenset({
    "EXECUTE", "PRE_SCRIPT_STARTED", "POST_SCRIPT_STARTED",
    "JOB_TERMINATED", "PRE_SCRIPT_TERMINATED", "POST_SCRIPT_TERMINATED",
})
# In the HTCondor queue (or about to be), waiting for a machine. An evicted or
# released job is back to waiting. PRE_SCRIPT_SUCCESS is here and not under
# "succeeded": it is the pre-script that succeeded, and the job itself has
# not been submitted yet.
_QUEUED = frozenset({
    "SUBMIT", "GRID_SUBMIT", "GLOBUS_SUBMIT", "JOB_EVICTED", "JOB_RELEASED",
    "PRE_SCRIPT_SUCCESS",
})

#: Every value a job's ``status`` takes. ``pending`` is a job DAGMan has not
#: released yet (no state at all); ``queued`` is one waiting in the queue;
#: ``held`` is one HTCondor has stopped until someone acts. The run header's
#: counts (workflow_detail) are sums over these, so the two cannot disagree:
#: they did when SUBMIT and JOB_HELD were labelled "running" here and counted
#: as queued there. The DAG viewer, lib/tone.ts and lib/vocabulary.ts in
#: studio-web know all six; a value added here needs adding there.
JOB_STATUSES = ("pending", "queued", "held", "running", "succeeded", "failed")


def _job_status(state: str | None, exitcode: Any) -> str:
    """Map a stampede job state + exitcode to one of JOB_STATUSES."""
    code = _as_int(exitcode)
    if code is not None:
        return "succeeded" if code == 0 else "failed"
    s = (state or "").upper()
    if s in _QUEUED:
        return "queued"
    if "SUCCESS" in s:
        return "succeeded"
    if "FAIL" in s:
        return "failed"
    if s in _EXECUTING:
        return "running"
    if s == "JOB_HELD":
        return "held"
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
                # The program's own exit code when a task failed (negative:
                # killed by that signal). `exitcode` is the job's, which
                # after the post-script is that script's verdict, usually 1.
                "app_exitcode": None,
                "status": "pending",
                "stdout_file": None,
                "stderr_file": None,
                "maxrss": None,
                "site": None,
                "duration": None,
                "stderr": None,
                "host": None,
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
        if ev.get("app_exitcode") is not None:
            entry["app_exitcode"] = ev["app_exitcode"]
        if ev.get("stdout_file"):
            entry["stdout_file"] = ev["stdout_file"]
        if ev.get("stderr_file"):
            entry["stderr_file"] = ev["stderr_file"]
        if ev.get("maxrss") is not None:
            entry["maxrss"] = ev["maxrss"]
        if ev.get("transformation"):
            entry["transformation"] = ev["transformation"]
        if ev.get("site"):
            entry["site"] = ev["site"]
        if ev.get("duration") is not None:
            entry["duration"] = ev["duration"]
        if ev.get("stderr"):
            entry["stderr"] = ev["stderr"]
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
    # Running means executing on a worker. Everything else unfinished (in the
    # HTCondor queue, held, or not yet released by DAGMan) is queued: counting
    # it as running showed a 200-step run as "200 running" with one slot busy.
    # The per-job status draws the same line, so the rows add up to this.
    unfinished = max(0, total - done - failed)
    running = min(
        unfinished, sum(1 for j in job_list if j["status"] == "running")
    )

    return {
        "status": status,
        "total_jobs": total,
        "completed_jobs": done,
        "failed_jobs": failed,
        "running_jobs": running,
        "queued_jobs": unfinished - running,
        "exec_site": None,
    }
