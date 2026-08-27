"""Zombie-run status derivation (#296).

An abnormal DAGMan death (log deleted mid-run, OOM kill, node reboot) takes
pegasus-monitord down with it, so no terminal marker is ever written: the
event stream ends mid-run, jobstate.log has no DAGMAN_FINISHED, the stampede
DB holds a lone WORKFLOW_STARTED. Every event-derived status then reads
"running" forever. The fix cross-checks the one file DAGMan itself always
closes out — ``*.dag.dagman.out`` ends every session with ``EXITING WITH
STATUS <n>`` — and only trusts "running" while no such exit is recorded.

Fixtures build the run dir shapes traced on the live instance in #296; no
Pegasus or Condor needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3

from services.workflow_scanner import dagman_exit_status, get_run_status


_EXITING_2 = (
    "**** condor_scheduniv_exec.672.0 (condor_DAGMAN) pid 225030 "
    "EXITING WITH STATUS 2\n"
)
_EXITING_0 = (
    "**** condor_scheduniv_exec.672.0 (condor_DAGMAN) pid 225030 "
    "EXITING WITH STATUS 0\n"
)
_STARTING_UP = "** condor_scheduniv_exec.673.0 (CONDOR_DAGMAN) STARTING UP\n"


def _write_dagman_out(run_dir, *chunks) -> None:
    with open(os.path.join(run_dir, "fabric-smoke-0.dag.dagman.out"), "w") as f:
        f.write("01/01/26 12:00:00 DAGMan chatter\n")
        for chunk in chunks:
            f.write(chunk)


def _write_events_without_end(run_dir) -> None:
    """A JSONL event log that starts a workflow and ends mid-run — the exact
    stream monitord leaves behind when DAGMan kills it."""
    events = [
        {"event_type": "workflow_start", "ts": 1.0},
        {"event_type": "jobs_init", "total_jobs": 4, "ts": 2.0},
        {"event_type": "job_state", "job_id": "split_ID1", "state": "EXECUTE",
         "ts": 3.0},
    ]
    with open(os.path.join(run_dir, "workflow-events.jsonl"), "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def _make_started_only_stampede_db(run_dir: str) -> None:
    """Stampede DB whose workflowstate holds only WORKFLOW_STARTED — what
    monitord leaves when DAGMan takes it down (the deployed-instance shape)."""
    with open(os.path.join(run_dir, "braindump.yml"), "w") as f:
        f.write(f"submit_dir: {run_dir}\n")
    con = sqlite3.connect(os.path.join(run_dir, "fabric-smoke-0.stampede.db"))
    con.executescript(
        """
        CREATE TABLE workflow (wf_id INTEGER, wf_uuid TEXT, dax_label TEXT,
            submit_dir TEXT, user TEXT, planner_version TEXT, timestamp REAL,
            parent_wf_id INTEGER);
        CREATE TABLE workflowstate (wf_id INTEGER, state TEXT, status INTEGER,
            timestamp REAL);
        CREATE TABLE job (job_id INTEGER, wf_id INTEGER, exec_job_id TEXT,
            type_desc TEXT);
        CREATE TABLE job_instance (job_id INTEGER, job_instance_id INTEGER,
            site TEXT, exitcode INTEGER, stdout_file TEXT, stderr_file TEXT,
            stderr_text TEXT, local_duration REAL, cluster_duration REAL,
            job_submit_seq INTEGER);
        CREATE TABLE jobstate (job_instance_id INTEGER, state TEXT,
            jobstate_submit_seq INTEGER);
        CREATE TABLE task (job_id INTEGER, transformation TEXT);
        CREATE TABLE invocation (job_instance_id INTEGER, exitcode INTEGER,
            maxrss INTEGER, transformation TEXT, remote_duration REAL);
        """
    )
    con.execute(
        "INSERT INTO workflow VALUES "
        "(1,'uuid','fabric-smoke',?, 'user','6.0',1.0,NULL)",
        (run_dir,),
    )
    con.execute(
        "INSERT INTO workflowstate VALUES (1,'WORKFLOW_STARTED',NULL,1.0)"
    )
    con.execute("INSERT INTO job VALUES (1,1,'split_ID1','compute')")
    con.commit()
    con.close()


# --- dagman_exit_status: the tail parser on its own ---------------------------


def test_no_dagman_out_means_alive(tmp_path):
    assert dagman_exit_status(str(tmp_path)) is None


def test_no_exiting_line_means_alive(tmp_path):
    _write_dagman_out(str(tmp_path), "more chatter\n")
    assert dagman_exit_status(str(tmp_path)) is None


def test_exit_nonzero_is_failed(tmp_path):
    _write_dagman_out(str(tmp_path), _EXITING_2)
    assert dagman_exit_status(str(tmp_path)) == "failed"


def test_exit_zero_is_succeeded(tmp_path):
    _write_dagman_out(str(tmp_path), _EXITING_0)
    assert dagman_exit_status(str(tmp_path)) == "succeeded"


def test_rescue_rerun_after_exit_is_alive(tmp_path):
    """A rescue/re-run appends a fresh session to the same file — an EXITING
    line with a later STARTING UP banner must not read as terminal."""
    _write_dagman_out(str(tmp_path), _EXITING_2, _STARTING_UP, "chatter\n")
    assert dagman_exit_status(str(tmp_path)) is None


def test_second_session_exit_wins(tmp_path):
    """...and when that re-run itself exits, the last exit is the verdict."""
    _write_dagman_out(str(tmp_path), _EXITING_2, _STARTING_UP, _EXITING_0)
    assert dagman_exit_status(str(tmp_path)) == "succeeded"


def test_exit_marker_beyond_tail_window_means_alive(tmp_path):
    """Only the tail is read (cheap per-run listing cost); an EXITING line
    buried megabytes back with fresh chatter after it doesn't count."""
    _write_dagman_out(str(tmp_path), _EXITING_2, "x" * (2 * 64 * 1024) + "\n")
    assert dagman_exit_status(str(tmp_path)) is None


# --- get_run_status: the event stream no longer wins unconditionally ----------


def test_zombie_run_with_jsonl_reads_failed(tmp_path):
    """#296 case 1: events without workflow_end + EXITING WITH STATUS 2."""
    _write_events_without_end(str(tmp_path))
    _write_dagman_out(str(tmp_path), _EXITING_2)
    assert get_run_status(str(tmp_path)) == "failed"


def test_zombie_run_clean_exit_reads_succeeded(tmp_path):
    """#296 case 2: same shape but DAGMan exited 0."""
    _write_events_without_end(str(tmp_path))
    _write_dagman_out(str(tmp_path), _EXITING_0)
    assert get_run_status(str(tmp_path)) == "succeeded"


def test_live_run_still_reads_running(tmp_path):
    """#296 case 3: no EXITING line → genuinely live, no regression."""
    _write_events_without_end(str(tmp_path))
    _write_dagman_out(str(tmp_path), "still going\n")
    assert get_run_status(str(tmp_path)) == "running"


def test_rescue_rerun_still_reads_running(tmp_path):
    """#296 case 4: EXITING followed by a later startup banner → running."""
    _write_events_without_end(str(tmp_path))
    _write_dagman_out(str(tmp_path), _EXITING_2, _STARTING_UP)
    assert get_run_status(str(tmp_path)) == "running"


def test_stampede_fallback_zombie_reads_failed(tmp_path):
    """#296 case 5: no JSONL at all — events synthesized from a stampede DB
    with a lone WORKFLOW_STARTED row, plus EXITING-with-2. The exact shape
    traced on the deployed instance."""
    _make_started_only_stampede_db(str(tmp_path))
    _write_dagman_out(str(tmp_path), _EXITING_2)
    assert get_run_status(str(tmp_path)) == "failed"


def test_finished_event_stream_is_untouched(tmp_path):
    """A stream with a real workflow_end keeps its own verdict — the guard
    only fires on 'running'."""
    events = [
        {"event_type": "workflow_start", "ts": 1.0},
        {"event_type": "workflow_end", "wf_status": 0, "ts": 9.0},
    ]
    with open(os.path.join(tmp_path, "workflow-events.jsonl"), "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    # A stale dagman.out from an earlier failed attempt must not override a
    # stream that reached its end.
    _write_dagman_out(str(tmp_path), _EXITING_2)
    assert get_run_status(str(tmp_path)) == "succeeded"


# --- get_workflow_detail: the path the runs list actually flows through -------


def test_runs_list_detail_reads_failed_for_zombie(tmp_path):
    """GET /api/workflows derives status via get_workflow_detail →
    event_detail, not get_run_status — the guard must reach it too, or the
    list keeps showing the zombie."""
    from services.workflow_monitor import get_workflow_detail

    _write_events_without_end(str(tmp_path))
    _write_dagman_out(str(tmp_path), _EXITING_2)

    detail = asyncio.run(get_workflow_detail(str(tmp_path)))
    assert detail["status"] == "failed"
    # Counts still come from the events — what ran before the death.
    assert detail["total_jobs"] == 4
