"""Per-job status labels and exit codes, as the Steps table shows them.

Both came out of the 2026-11-10 trial against ``pegasus-monitor`` on the same
stampede DB. A run with one held and one unmatchable idle job had a header
saying "0 running, 7 queued" over rows that called both jobs "running"; and a
job that exited 1 showed exit code 256, the raw wait status monitord stores.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from services import event_reader, run_diagnosis, runtime_estimate
from services.stampede_reader import (
    decode_wait_status,
    read_stampede_events,
    read_stampede_job_details,
)


def _events(states: dict[str, str | None], exitcodes: dict[str, int] | None = None):
    exitcodes = exitcodes or {}
    evs = [
        {
            "event_type": "jobs_init",
            "total_jobs": len(states),
            "jobs": [{"job_id": j} for j in states],
        }
    ]
    for j, st in states.items():
        if st is None:
            continue
        ev = {"event_type": "job_state", "job_id": j, "state": st}
        if j in exitcodes:
            ev["exitcode"] = exitcodes[j]
        evs.append(ev)
    return evs


# --- B1: the label ------------------------------------------------------------


@pytest.mark.parametrize(
    "state, expected",
    [
        (None, "pending"),
        ("PRE_SCRIPT_STARTED", "running"),
        ("PRE_SCRIPT_SUCCESS", "queued"),  # the pre-script, not the job
        ("SUBMIT", "queued"),
        ("GRID_SUBMIT", "queued"),
        ("EXECUTE", "running"),
        ("JOB_HELD", "held"),
        ("JOB_RELEASED", "queued"),
        ("JOB_EVICTED", "queued"),
        ("JOB_TERMINATED", "running"),
        ("JOB_SUCCESS", "succeeded"),
        ("POST_SCRIPT_STARTED", "running"),
        ("POST_SCRIPT_SUCCESS", "succeeded"),
        ("JOB_FAILURE", "failed"),
        ("POST_SCRIPT_FAILURE", "failed"),
        ("PRE_SCRIPT_FAILURE", "failed"),
        ("SUBMIT_FAILED", "failed"),
        ("SOMETHING_NEW", "pending"),
    ],
)
def test_job_state_maps_to_one_of_the_statuses(state, expected):
    assert event_reader._job_status(state, None) == expected
    assert expected in event_reader.JOB_STATUSES


def test_exit_code_decides_over_the_state():
    assert event_reader._job_status("JOB_HELD", 0) == "succeeded"
    assert event_reader._job_status("SUBMIT", 1) == "failed"
    assert event_reader._job_status("SUBMIT", -9) == "failed"


def test_held_and_idle_jobs_are_not_labelled_running():
    """The trial's `held` case: proc_holdme held, proc_idleme unmatchable."""
    jobs = event_reader.jobs(
        _events({"proc_quick": "POST_SCRIPT_SUCCESS", "proc_idleme": "SUBMIT",
                 "proc_holdme": "JOB_HELD", "proc_later": None}, {"proc_quick": 0})
    )
    assert {j["job_id"]: j["status"] for j in jobs} == {
        "proc_quick": "succeeded",
        "proc_idleme": "queued",
        "proc_holdme": "held",
        "proc_later": "pending",
    }


def test_header_counts_are_the_sum_of_the_row_labels():
    events = _events(
        {
            "a": "POST_SCRIPT_SUCCESS", "b": "EXECUTE", "c": "POST_SCRIPT_STARTED",
            "d": "SUBMIT", "e": "JOB_HELD", "f": None, "g": "JOB_FAILURE",
            "h": "JOB_TERMINATED", "i": "JOB_EVICTED",
        },
        {"a": 0, "g": 1},
    )
    detail = event_reader.workflow_detail(events)
    rows = [j["status"] for j in event_reader.jobs(events)]
    assert detail["running_jobs"] == rows.count("running") == 3
    assert detail["queued_jobs"] == sum(
        rows.count(s) for s in ("pending", "queued", "held")
    ) == 4
    assert detail["completed_jobs"] == rows.count("succeeded") == 1
    assert detail["failed_jobs"] == rows.count("failed") == 1


def test_time_left_still_counts_queued_and_held_steps(monkeypatch):
    """The estimate listed remaining work as status pending or running; with
    the new labels a queue full of waiting steps would have read as done."""
    events = _events(
        {"a": "POST_SCRIPT_SUCCESS", "b": "SUBMIT", "c": "JOB_HELD", "d": None},
        {"a": 0},
    )
    events.append(
        {"event_type": "job_state", "job_id": "a", "state": "POST_SCRIPT_SUCCESS",
         "duration": 10.0}
    )
    monkeypatch.setattr(runtime_estimate, "read_events", lambda _d: events)
    estimate = runtime_estimate.estimate_remaining("/run")
    assert estimate["remaining_jobs"] == 3
    assert estimate["remaining_seconds"] == 30


def test_graph_rollup_treats_queued_and_held_nodes_as_a_live_run():
    from routers.workflows import _derive_workflow_status

    for status in ("queued", "held"):
        nodes = [{"id": "a", "status": "succeeded"}, {"id": "b", "status": status}]
        assert _derive_workflow_status(nodes) == "running"


# --- B2: the exit code ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw, decoded",
    [
        (None, None),
        (0, 0),
        (256, 1),  # exit 1
        (35072, 137),  # a wrapper that exited 137
        (9, -9),  # killed by SIGKILL
        (139, -11),  # SIGSEGV with the core-dump bit
        (-1, -128),  # failed, nothing to report
        (128, 128),  # no real wait status; must not decode to 0
        ("256", "256"),  # not a stampede integer: left alone
    ],
)
def test_decode_wait_status(raw, decoded):
    assert decode_wait_status(raw) == decoded


def test_decoding_never_turns_a_failure_into_a_success():
    for raw in list(range(-3, 700)) + [35072, 65280]:
        assert (decode_wait_status(raw) == 0) == (raw == 0)


def _db(run_dir: str, jobs: list[tuple]) -> None:
    """jobs: (name, job_instance.exitcode, state, [(transformation, raw)])."""
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "braindump.yml"), "w") as f:
        f.write(f"submit_dir: {run_dir}\n")
    con = sqlite3.connect(os.path.join(run_dir, "pm-fail-0.stampede.db"))
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
        "INSERT INTO workflow VALUES (1,'uuid','pm-fail',?, 'submit','6.0',1.0,NULL)",
        (run_dir,),
    )
    con.execute("INSERT INTO workflowstate VALUES (1,'WORKFLOW_STARTED',NULL,1.0)")
    for n, (name, code, state, invocations) in enumerate(jobs, start=1):
        con.execute("INSERT INTO job VALUES (?,1,?,'compute')", (n, name))
        con.execute(
            "INSERT INTO job_instance VALUES (?,?,'condorpool',?,NULL,NULL,"
            "'stderr text',5.0,NULL,1)",
            (n, 100 + n, code),
        )
        con.execute("INSERT INTO jobstate VALUES (?,?,1)", (100 + n, state))
        for tx, raw in invocations:
            con.execute(
                "INSERT INTO invocation VALUES (?,?,NULL,?,1.0)", (100 + n, raw, tx)
            )
    con.commit()
    con.close()


def _roster(run_dir: str) -> dict[str, dict]:
    return {j["job_id"]: j for j in event_reader.jobs(read_stampede_events(run_dir))}


def test_exit_code_is_shown_decoded_not_as_the_raw_wait_status(tmp_path):
    """The trial's failed job: stored 256, native says exit code 1."""
    run_dir = str(tmp_path)
    _db(run_dir, [("proc_bad", 256, "POST_SCRIPT_FAILURE", [("proc", 256)])])
    job = _roster(run_dir)["proc_bad"]
    assert job["exitcode"] == 1
    assert job["status"] == "failed"
    assert job["stderr"] == "stderr text"  # still attached to a failed job


def test_success_and_failure_survive_the_decoding(tmp_path):
    run_dir = str(tmp_path)
    _db(
        run_dir,
        [
            ("ok", 0, "POST_SCRIPT_SUCCESS", [("proc", 0)]),
            ("bad", 256, "POST_SCRIPT_FAILURE", [("proc", 256)]),
            ("nocode", -1, "JOB_FAILURE", []),
        ],
    )
    roster = _roster(run_dir)
    assert roster["ok"]["status"] == "succeeded" and roster["ok"]["exitcode"] == 0
    assert roster["ok"]["stderr"] is None
    assert roster["bad"]["status"] == "failed"
    assert roster["nocode"]["status"] == "failed"
    assert roster["nocode"]["exitcode"] == -128


def test_the_programs_own_exit_code_is_kept_beside_the_jobs(tmp_path):
    """After the post-script the job's code is that script's 1; what the
    program did is in its invocation, past the pre/post-script rows."""
    run_dir = str(tmp_path)
    _db(
        run_dir,
        [
            ("oom_wrapper", 256, "POST_SCRIPT_FAILURE",
             [("dagman::pre", 0), ("proc", 35072), ("dagman::post", 256)]),
            ("sigkilled", 256, "POST_SCRIPT_FAILURE", [("proc", 9)]),
            ("post_only", 256, "POST_SCRIPT_FAILURE",
             [("proc", 0), ("dagman::post", 256)]),
            ("ok", 0, "POST_SCRIPT_SUCCESS", [("proc", 0)]),
        ],
    )
    roster = _roster(run_dir)
    assert (roster["oom_wrapper"]["exitcode"], roster["oom_wrapper"]["app_exitcode"]) == (1, 137)
    assert (roster["sigkilled"]["exitcode"], roster["sigkilled"]["app_exitcode"]) == (1, -9)
    assert roster["post_only"]["app_exitcode"] is None
    assert roster["ok"]["app_exitcode"] is None

    details = read_stampede_job_details(run_dir)
    assert details["oom_wrapper"]["app_exitcode"] == 137
    assert details["sigkilled"]["app_exitcode"] == -9


def test_out_of_memory_is_recognised_from_the_stampede_db(tmp_path):
    """137 was only ever matched in stderr: the DB handed over 256."""
    run_dir = str(tmp_path)
    _db(
        run_dir,
        [
            ("oom_wrapper", 256, "POST_SCRIPT_FAILURE", [("wrapped", 35072)]),
            ("sigkilled", 256, "POST_SCRIPT_FAILURE", [("killed", 9)]),
            ("plain", 256, "POST_SCRIPT_FAILURE", [("plain", 256)]),
        ],
    )
    jobs = list(_roster(run_dir).values())
    for j in jobs:
        j["stderr"] = ""  # the exit code alone has to carry it
    # Cards are named after the transformation, here the invocation's.
    cards = {c["step"]: c for c in run_diagnosis.diagnose_jobs(jobs)}
    assert "memory" in cards["wrapped"]["cause"]
    assert cards["wrapped"]["exitcode"] == 137
    assert "memory" in cards["killed"]["cause"]
    assert cards["killed"]["exitcode"] == -9
    assert "memory" not in cards["plain"]["cause"]
    assert cards["plain"]["exitcode"] == 1
    assert run_diagnosis.categorize_jobs(jobs[1:2]) == "out_of_memory"


def test_a_signal_is_described_in_words():
    assert run_diagnosis.describe_exit(-9) == "killed by signal 9"
    assert run_diagnosis.describe_exit(137) == "137"
    assert run_diagnosis.describe_exit(-128) == "-128"
    cause, _ = run_diagnosis.classify_job_failure("", -9)
    assert "memory" in cause
    cause, _ = run_diagnosis.classify_job_failure("", 1, 137)
    assert "memory" in cause
    cause, _ = run_diagnosis.classify_job_failure("", 1)
    assert "memory" not in cause
