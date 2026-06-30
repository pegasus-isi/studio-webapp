"""Tests for the stampede-DB fallback reader."""

from __future__ import annotations

import os
import sqlite3

from services import event_reader
from services.stampede_reader import read_stampede_events


def _make_stampede_db(run_dir: str) -> None:
    """Create a minimal stampede DB mirroring a failed csv-summary run:
    8 abstract jobs, 3 succeeded, 1 compute failed (exit 256), 4 never ran.
    """
    os.makedirs(run_dir, exist_ok=True)
    # braindump points submit_dir at run_dir so the reader finds the DB.
    with open(os.path.join(run_dir, "braindump.yml"), "w") as f:
        f.write(f"submit_dir: {run_dir}\n")

    db = os.path.join(run_dir, "csv_summary-0.stampede.db")
    con = sqlite3.connect(db)
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
            job_submit_seq INTEGER);
        CREATE TABLE jobstate (job_instance_id INTEGER, state TEXT,
            jobstate_submit_seq INTEGER);
        CREATE TABLE task (job_id INTEGER, transformation TEXT);
        CREATE TABLE invocation (job_instance_id INTEGER, exitcode INTEGER,
            maxrss INTEGER, transformation TEXT);
        """
    )
    con.execute(
        "INSERT INTO workflow VALUES (1,'uuid','csv_summary',?, 'kthare10','6.0',1.0,NULL)",
        (run_dir,),
    )
    con.executemany(
        "INSERT INTO workflowstate VALUES (1,?,?,?)",
        [("WORKFLOW_STARTED", None, 1.0), ("WORKFLOW_TERMINATED", 1, 9.0)],
    )
    # 8 jobs: 3 ran ok, 1 compute failed, 4 never produced an instance.
    jobs = [
        (1, "summarize_csv_x", "compute"),
        (2, "stage_in_x", "stage-in-tx"),
        (3, "create_dir_x", "create-dir"),
        (4, "stage_in_y", "stage-in-tx"),
        (5, "cleanup_a", "cleanup"),
        (6, "stage_out_x", "stage-out-tx"),
        (7, "cleanup_b", "cleanup"),
        (8, "cleanup_c", "cleanup"),
    ]
    con.executemany("INSERT INTO job VALUES (?,1,?,?)", jobs)
    con.execute("INSERT INTO task VALUES (1,'summarize_csv')")
    # instances: job1 failed (256), jobs 2/3/4 succeeded (0)
    instances = [
        (1, 101, "condorpool", 256, "out1", "err1", 4),
        (2, 102, "local", 0, "out2", "err2", 1),
        (3, 103, "local", 0, "out3", "err3", 2),
        (4, 104, "local", 0, "out4", "err4", 3),
    ]
    con.executemany("INSERT INTO job_instance VALUES (?,?,?,?,?,?,?)", instances)
    con.executemany(
        "INSERT INTO jobstate VALUES (?,?,?)",
        [
            (101, "POST_SCRIPT_FAILED", 4),
            (102, "POST_SCRIPT_SUCCESS", 1),
            (103, "POST_SCRIPT_SUCCESS", 2),
            (104, "POST_SCRIPT_SUCCESS", 3),
        ],
    )
    con.executemany(
        "INSERT INTO invocation VALUES (?,?,?,?)",
        [(101, 256, 2048, "summarize_csv"), (102, 0, 512, "pegasus::transfer")],
    )
    con.commit()
    con.close()


def test_stampede_fallback_reconstructs_jobs_and_status(tmp_path):
    run_dir = str(tmp_path / "run0002")
    _make_stampede_db(run_dir)

    events = read_stampede_events(run_dir)
    assert events, "expected synthesized events from stampede DB"

    # Overall workflow status reflects the TERMINATED status=1 -> failed.
    assert event_reader.workflow_status(events) == "failed"

    jobs = event_reader.jobs(events)
    assert len(jobs) == 8  # all abstract jobs listed
    by_name = {j["job_id"]: j for j in jobs}

    # The compute job failed (exit 256), transformation surfaced.
    compute = by_name["summarize_csv_x"]
    assert compute["status"] == "failed"
    assert compute["transformation"] == "summarize_csv"
    assert compute["maxrss"] == 2048

    # Jobs that produced a 0-exit instance succeeded.
    assert by_name["stage_in_x"]["status"] == "succeeded"
    # Jobs that never ran stay pending (DAG would render them gray).
    assert by_name["cleanup_a"]["status"] == "pending"

    detail = event_reader.workflow_detail(events)
    assert detail["status"] == "failed"
    assert detail["total_jobs"] == 8
    assert detail["completed_jobs"] == 3
    assert detail["failed_jobs"] == 1


def test_read_events_uses_stampede_when_no_jsonl(tmp_path):
    """read_events() transparently falls back to the stampede DB."""
    run_dir = str(tmp_path / "run0003")
    _make_stampede_db(run_dir)
    events = event_reader.read_events(run_dir)
    assert event_reader.workflow_status(events) == "failed"
    assert len(event_reader.jobs(events)) == 8
