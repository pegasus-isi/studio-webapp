"""One run directory, one workflow: restarts and hierarchical runs.

Fixtures are modelled on the stampede DBs of the 2026-11-10 trial (Pegasus
6.0.0.dev0, HTCondor 25.14.1), where Studio's reader disagreed with
``pegasus-monitor`` reading the same file:

* A DAGMan the schedd restarted wrote STARTED, TERMINATED 2, STARTED,
  TERMINATED 0. Studio took the first TERMINATED and said "failed" for a run
  that finished 17/17.
* A hierarchical run keeps the root (wf_id 1) and its sub-workflow (wf_id 2)
  in the root's DB. With no wf_id in any query the root read the
  sub-workflow's TERMINATED as its own ("succeeded" mid-run), listed both
  workflows' jobs as one roster with four names twice, and the sub-workflow's
  own directory, which has no DB, read "planning" with no jobs.
* A DAGMan killed with SIGKILL writes no EXITING line in dagman.out; the
  schedd's event log for the DAGMan job is what records its end.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3

from services import event_reader, workflow_monitor
from services.slow_steps import slow_steps
from services.stampede_reader import (
    find_stampede_db,
    read_stampede_events,
    read_stampede_job_details,
)
from services.workflow_scanner import (
    _jobstate_terminal_status,
    dagman_exit_status,
    dagman_job_log_status,
    get_run_status,
)

ROOT_UUID = "4b64b124-a684-4c72-9692-017290752be8"
SUB_UUID = "96546283-f8d6-4ab5-bdbc-a1da8d93b8d8"
NOW = 1_794_331_000.0

_SCHEMA = """
    CREATE TABLE workflow (wf_id INTEGER PRIMARY KEY, wf_uuid TEXT,
        dax_label TEXT, submit_dir TEXT, user TEXT, planner_version TEXT,
        timestamp REAL, parent_wf_id INTEGER, root_wf_id INTEGER);
    CREATE TABLE workflowstate (wf_id INTEGER, state TEXT, timestamp REAL,
        restart_count INTEGER, status INTEGER,
        PRIMARY KEY (wf_id, state, timestamp));
    CREATE TABLE job (job_id INTEGER PRIMARY KEY, wf_id INTEGER,
        exec_job_id TEXT, type_desc TEXT, UNIQUE (wf_id, exec_job_id));
    CREATE TABLE job_instance (job_instance_id INTEGER PRIMARY KEY,
        job_id INTEGER, host_id INTEGER, job_submit_seq INTEGER, site TEXT,
        exitcode INTEGER, stdout_file TEXT, stderr_file TEXT,
        stderr_text TEXT, local_duration REAL, cluster_duration REAL,
        subwf_id INTEGER);
    CREATE TABLE jobstate (job_instance_id INTEGER, state TEXT,
        timestamp REAL, jobstate_submit_seq INTEGER);
    CREATE TABLE task (task_id INTEGER PRIMARY KEY, wf_id INTEGER,
        job_id INTEGER, transformation TEXT);
    CREATE TABLE invocation (invocation_id INTEGER PRIMARY KEY,
        wf_id INTEGER, job_instance_id INTEGER, task_submit_seq INTEGER,
        exitcode INTEGER, maxrss INTEGER, transformation TEXT,
        remote_duration REAL);
"""


def _braindump(run_dir: str, wf_uuid: str, root_uuid: str, submit_dir: str) -> None:
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "braindump.yml"), "w") as f:
        f.write(
            f'root_wf_uuid: "{root_uuid}"\nwf_uuid: "{wf_uuid}"\n'
            f'submit_dir: "{submit_dir}"\n'
        )


def _flat_run(run_dir: str, states: list[tuple[str, float, int, int | None]]) -> None:
    """A single-workflow run: two finished jobs and the given workflowstate
    rows as (state, timestamp, restart_count, status)."""
    _braindump(run_dir, ROOT_UUID, ROOT_UUID, run_dir)
    con = sqlite3.connect(os.path.join(run_dir, "pm-zombie-0.stampede.db"))
    con.executescript(_SCHEMA)
    con.execute(
        "INSERT INTO workflow VALUES (1,?,'pm-zombie',?,'submit','6.0',1.0,NULL,1)",
        (ROOT_UUID, run_dir),
    )
    con.executemany(
        "INSERT INTO workflowstate VALUES (1,?,?,?,?)",
        states,
    )
    for n, name in enumerate(("proc_preprocess", "proc_analyze"), start=1):
        con.execute("INSERT INTO job VALUES (?,1,?,'compute')", (n, name))
        con.execute(
            "INSERT INTO job_instance VALUES (?,?,NULL,?,'condorpool',0,"
            "NULL,NULL,NULL,45.0,NULL,NULL)",
            (n, n, n),
        )
        con.execute(
            "INSERT INTO jobstate VALUES (?,'POST_SCRIPT_SUCCESS',?,9)", (n, 50.0 + n)
        )
    con.commit()
    con.close()


def _detail(run_dir: str) -> dict:
    return asyncio.run(workflow_monitor.get_workflow_detail(run_dir))


# --- A1: a restarted run ------------------------------------------------------

_RESTARTED_OK = [
    ("WORKFLOW_STARTED", 1794330327.0, 0, None),
    ("WORKFLOW_TERMINATED", 1794330376.0, 0, 2),
    ("WORKFLOW_STARTED", 1794330377.0, 1, None),
    ("WORKFLOW_TERMINATED", 1794330588.0, 1, 0),
]


def test_restart_that_then_succeeds_reads_succeeded(tmp_path):
    """Trial case zombie3: 17/17 done, EXITING WITH STATUS 0, read "failed"."""
    _flat_run(str(tmp_path), _RESTARTED_OK)
    events = read_stampede_events(str(tmp_path))
    assert event_reader.workflow_status(events) == "succeeded"
    assert get_run_status(str(tmp_path)) == "succeeded"
    assert _detail(str(tmp_path))["status"] == "succeeded"


def test_restarted_run_reads_running_while_it_runs(tmp_path):
    _flat_run(str(tmp_path), _RESTARTED_OK[:3])
    events = read_stampede_events(str(tmp_path))
    assert event_reader.workflow_status(events) == "running"
    assert _detail(str(tmp_path))["status"] == "running"


def test_restart_in_the_same_second_as_the_termination_is_running(tmp_path):
    """Whole-second timestamps tie; the rows' insertion order settles it."""
    _flat_run(
        str(tmp_path),
        [
            ("WORKFLOW_STARTED", 100.0, 0, None),
            ("WORKFLOW_TERMINATED", 200.0, 0, 2),
            ("WORKFLOW_STARTED", 200.0, 1, None),
        ],
    )
    assert (
        event_reader.workflow_status(read_stampede_events(str(tmp_path))) == "running"
    )


def test_single_session_failure_still_reads_failed(tmp_path):
    _flat_run(str(tmp_path), _RESTARTED_OK[:2])
    assert event_reader.workflow_status(read_stampede_events(str(tmp_path))) == "failed"


def test_jobstate_log_restart_is_running_not_failed(tmp_path):
    """The same sequence in jobstate.log, the status source with no DB."""
    (tmp_path / "jobstate.log").write_text(
        "1794330244 INTERNAL *** MONITORD_STARTED ***\n"
        "1794330132 INTERNAL *** DAGMAN_STARTED 112.0 ***\n"
        "1794330241 INTERNAL *** DAGMAN_FINISHED 2 ***\n"
        "1794330242 INTERNAL *** DAGMAN_STARTED 112.0 ***\n"
        "1794330273 proc_findrange_1 JOB_SUCCESS 0 condorpool - 7\n"
    )
    assert _jobstate_terminal_status(str(tmp_path)) == "running"
    with open(tmp_path / "jobstate.log", "a") as f:
        f.write("1794330588 INTERNAL *** DAGMAN_FINISHED 0 ***\n")
    assert _jobstate_terminal_status(str(tmp_path)) == "succeeded"


# --- the SIGKILL zombie: the schedd's log for the DAGMan job --------------------

_SUBMITTED = (
    "000 (112.000.000) 2026-11-10 17:02:08 Job submitted from host: <172.17.0.2:9618>\n"
    "...\n"
    "001 (112.000.000) 2026-11-10 17:02:12 Job executing on host: <172.17.0.2:9618>\n"
    "...\n"
)
_KILLED_AND_REQUEUED = (
    "004 (112.000.000) 2026-11-10 17:04:01 Job was evicted.\n"
    "\t(0) Job terminated and was requeued\n"
    "\t\tUsr 0 00:00:00, Sys 0 00:00:00  -  Run Remote Usage\n"
    "\t(0) Abnormal termination (signal 9)\n"
    "\t(0) No core file\n"
    "\tThe job attribute OnExitRemove expression '(ExitSignal =?= 11 || "
    "(ExitCode =!= undefined && ExitCode >= 0 && ExitCode <= 2))' evaluated to FALSE\n"
    "...\n"
    "001 (112.000.000) 2026-11-10 17:04:02 Job executing on host: <172.17.0.2:9618>\n"
    "...\n"
)
_REMOVED = (
    "004 (112.000.000) 2026-11-10 17:04:37 Job was evicted.\n"
    "\t(0) CPU times\n"
    "\t0  -  Run Bytes Sent By Job\n"
    "...\n"
    "009 (112.000.000) 2026-11-10 17:04:37 Job was aborted.\n"
    "\tvia condor_rm (by user submit)\n"
    "...\n"
)


def _terminated(how: str) -> str:
    return (
        "005 (152.000.000) 2026-11-10 17:10:02 Job terminated.\n"
        f"\t(1) {how}\n"
        "\t\tUsr 0 00:00:00, Sys 0 00:00:00  -  Run Remote Usage\n"
        "\t0  -  Total Bytes Received By Job\n"
        "...\n"
    )


def _job_log(run_dir, *chunks: str) -> None:
    with open(os.path.join(str(run_dir), "pm-zombie-0.dag.dagman.log"), "w") as f:
        f.write("".join(chunks))


def test_job_log_absent_or_still_queued_is_alive(tmp_path):
    assert dagman_job_log_status(str(tmp_path)) is None
    _job_log(tmp_path, _SUBMITTED)
    assert dagman_job_log_status(str(tmp_path)) is None


def test_killed_dagman_the_schedd_requeued_is_alive(tmp_path):
    """SIGKILL does not end the run: OnExitRemove is false, the schedd starts
    DAGMan again. Reading the 004 as an ending would fail a live run."""
    _job_log(tmp_path, _SUBMITTED, _KILLED_AND_REQUEUED)
    assert dagman_job_log_status(str(tmp_path)) is None


def test_removed_dagman_is_failed(tmp_path):
    _job_log(tmp_path, _SUBMITTED, _KILLED_AND_REQUEUED, _REMOVED)
    assert dagman_job_log_status(str(tmp_path)) == "failed"


def test_terminated_dagman_reads_its_return_value(tmp_path):
    _job_log(tmp_path, _SUBMITTED, _terminated("Normal termination (return value 0)"))
    assert dagman_job_log_status(str(tmp_path)) == "succeeded"
    _job_log(tmp_path, _SUBMITTED, _terminated("Normal termination (return value 1)"))
    assert dagman_job_log_status(str(tmp_path)) == "failed"
    _job_log(tmp_path, _SUBMITTED, _terminated("Abnormal termination (signal 11)"))
    assert dagman_job_log_status(str(tmp_path)) == "failed"


def test_resubmission_after_an_ending_is_alive_again(tmp_path):
    _job_log(tmp_path, _SUBMITTED, _REMOVED, _SUBMITTED.replace("112", "160"))
    assert dagman_job_log_status(str(tmp_path)) is None


def test_killed_restarted_then_removed_run_reads_failed(tmp_path):
    """Trial case zombie2. The DB ends on the restart's WORKFLOW_STARTED and
    dagman.out has two STARTING UP banners and no EXITING line, so neither
    can say the run is over; before the last-state rule it read "failed"
    only because the first TERMINATED row was taken."""
    run_dir = str(tmp_path)
    _flat_run(run_dir, _RESTARTED_OK[:3])
    with open(os.path.join(run_dir, "pm-zombie-0.dag.dagman.out"), "w") as f:
        f.write(
            "11/10/26 17:02:12 ** condor_scheduniv_exec.112.0 (CONDOR_DAGMAN) STARTING UP\n"
            "11/10/26 17:04:02 ** condor_scheduniv_exec.112.0 (CONDOR_DAGMAN) STARTING UP\n"
            "11/10/26 17:04:33 Initializing user log writer\n"
        )
    assert get_run_status(run_dir) == "running"

    _job_log(run_dir, _SUBMITTED, _KILLED_AND_REQUEUED, _REMOVED)
    assert dagman_exit_status(run_dir) == "failed"
    assert get_run_status(run_dir) == "failed"
    assert _detail(run_dir)["status"] == "failed"


def test_dagman_out_verdict_is_not_overridden_by_the_job_log(tmp_path):
    """A rescue re-run is live in dagman.out (banner after the exit) and in
    the job log (a fresh 000); an old 009 before it changes nothing."""
    run_dir = str(tmp_path)
    with open(os.path.join(run_dir, "pm-zombie-0.dag.dagman.out"), "w") as f:
        f.write(
            "**** condor_scheduniv_exec.112.0 (condor_DAGMAN) pid 1 EXITING WITH STATUS 2\n"
            "** condor_scheduniv_exec.160.0 (CONDOR_DAGMAN) STARTING UP\n"
        )
    _job_log(run_dir, _SUBMITTED, _REMOVED, _SUBMITTED.replace("112", "160"))
    assert dagman_exit_status(run_dir) is None


# --- A2/A3: hierarchical runs ---------------------------------------------------


def _hierarchy(tmp_path, sub_recorded: bool = True) -> tuple[str, str]:
    """Root (wf 1) mid-run with its sub-workflow (wf 2) already finished,
    in one DB in the root's directory. Returns (root_dir, sub_dir)."""
    root_dir = str(tmp_path / "run0001")
    sub_dir = os.path.join(root_dir, "00", "00", "pm-inner_subwf.000")
    _braindump(root_dir, ROOT_UUID, ROOT_UUID, root_dir)
    # Pegasus records the sub-workflow's submit_dir with a "/./" in it.
    _braindump(sub_dir, SUB_UUID, ROOT_UUID, f"{root_dir}/00/00/./pm-inner_subwf.000")

    con = sqlite3.connect(os.path.join(root_dir, "pm-root-0.stampede.db"))
    con.executescript(_SCHEMA)
    con.execute(
        "INSERT INTO workflow VALUES (1,?,'pm-root',?,'submit','6.0',10.0,NULL,1)",
        (ROOT_UUID, root_dir),
    )
    con.execute("INSERT INTO workflowstate VALUES (1,'WORKFLOW_STARTED',10.0,0,NULL)")
    if sub_recorded:
        con.execute(
            "INSERT INTO workflow VALUES (2,?,'pm-inner',?,'submit','6.0',60.0,1,1)",
            (SUB_UUID, sub_dir),
        )
        con.executemany(
            "INSERT INTO workflowstate VALUES (2,?,?,0,?)",
            [("WORKFLOW_STARTED", 60.0, None), ("WORKFLOW_TERMINATED", 160.0, 0)],
        )

    # (job_id, wf_id, exec_job_id, type_desc)
    jobs = [
        (1, 1, "create_dir_pm-root_0_local", "create dir"),
        (2, 1, "proc_outer_a", "compute"),
        (3, 1, "pegasus-plan_subwf", "dax"),
        (4, 1, "register_local_0_0", "registration"),
        (5, 1, "proc_outer_b", "compute"),
    ]
    if sub_recorded:
        jobs += [
            (11, 2, "proc_inner_a", "compute"),
            (12, 2, "proc_inner_b", "compute"),
            (13, 2, "register_local_0_0", "registration"),
        ]
    con.executemany("INSERT INTO job VALUES (?,?,?,?)", jobs)
    con.executemany(
        "INSERT INTO task VALUES (?,?,?,?)",
        [(1, 1, 2, "proc"), (2, 1, 5, "proc")]
        + ([(3, 2, 11, "proc"), (4, 2, 12, "proc")] if sub_recorded else []),
    )

    # (jiid, job_id, seq, site, exitcode, local_duration, subwf_id, last state, at)
    instances = [
        (1, 1, 1, "local", 0, 2.0, None, "POST_SCRIPT_SUCCESS", 20.0),
        (2, 2, 2, "condorpool", 0, 10.0, None, "POST_SCRIPT_SUCCESS", 40.0),
        # The sub-workflow job mid-run: pegasus-plan (its pre-script) is
        # done, the DAGMan job is in the queue, no exit code yet.
        (3, 3, 3, "local", None, None, 2, "SUBMIT", 55.0),
    ]
    if sub_recorded:
        instances += [
            (11, 11, 1, "condorpool", 0, 10.0, None, "POST_SCRIPT_SUCCESS", 90.0),
            # Still executing as far as monitord's last word goes, and long
            # past its usual 10 s: the sub-workflow's slow step, not the root's.
            (12, 12, 2, "condorpool", None, None, None, "EXECUTE", NOW - 2400),
            (13, 13, 3, "local", 0, 7.0, None, "POST_SCRIPT_SUCCESS", 150.0),
        ]
    for jiid, job_id, seq, site, code, dur, subwf, state, at in instances:
        con.execute(
            "INSERT INTO job_instance VALUES (?,?,NULL,?,?,?,NULL,NULL,NULL,?,NULL,?)",
            (jiid, job_id, seq, site, code, dur, subwf),
        )
        con.execute("INSERT INTO jobstate VALUES (?,'SUBMIT',?,1)", (jiid, at - 5))
        if state != "SUBMIT":
            con.execute("INSERT INTO jobstate VALUES (?,?,?,2)", (jiid, state, at))
    # The pre-script's invocation, exit 0, exists while the job is queued.
    con.execute("INSERT INTO invocation VALUES (1,1,3,-1,0,NULL,'dagman::pre',5.0)")
    con.commit()
    con.close()
    return root_dir, sub_dir


def test_root_is_running_while_only_its_sub_workflow_has_terminated(tmp_path):
    root_dir, _ = _hierarchy(tmp_path)
    events = read_stampede_events(root_dir)
    assert event_reader.workflow_status(events) == "running"
    detail = _detail(root_dir)
    assert detail["status"] == "running"
    assert (detail["total_jobs"], detail["completed_jobs"]) == (5, 2)


def test_root_roster_is_the_roots_jobs_with_the_sub_workflow_as_one(tmp_path):
    root_dir, _ = _hierarchy(tmp_path)
    jobs = asyncio.run(workflow_monitor.get_workflow_jobs(root_dir))
    names = [j["job_id"] for j in jobs]
    assert names == [
        "create_dir_pm-root_0_local",
        "proc_outer_a",
        "pegasus-plan_subwf",
        "register_local_0_0",
        "proc_outer_b",
    ]
    by_name = {j["job_id"]: j for j in jobs}
    # No instance for the root's own register job yet: the sub-workflow's
    # finished job of the same name must not stand in for it.
    assert by_name["register_local_0_0"]["status"] == "pending"
    assert by_name["register_local_0_0"]["duration"] is None


def test_queued_sub_workflow_job_is_not_succeeded_by_its_pre_script(tmp_path):
    root_dir, _ = _hierarchy(tmp_path)
    jobs = asyncio.run(workflow_monitor.get_workflow_jobs(root_dir))
    subwf = next(j for j in jobs if j["job_id"] == "pegasus-plan_subwf")
    assert subwf["state"] == "SUBMIT"
    assert subwf["exitcode"] is None
    assert subwf["status"] not in ("succeeded", "failed")


def test_sub_workflow_directory_reads_its_own_workflow_from_the_root_db(tmp_path):
    root_dir, sub_dir = _hierarchy(tmp_path)
    assert find_stampede_db(sub_dir) == os.path.join(root_dir, "pm-root-0.stampede.db")

    events = read_stampede_events(sub_dir)
    assert event_reader.workflow_status(events) == "succeeded"
    assert events[0]["dax_label"] == "pm-inner"
    assert [j["job_id"] for j in event_reader.jobs(events)] == [
        "proc_inner_a",
        "proc_inner_b",
        "register_local_0_0",
    ]
    assert get_run_status(sub_dir) == "succeeded"

    details = read_stampede_job_details(sub_dir)
    assert set(details) == {"proc_inner_a", "proc_inner_b", "register_local_0_0"}
    assert details["register_local_0_0"]["duration"] == 7.0
    assert read_stampede_job_details(root_dir)["register_local_0_0"]["duration"] is None


def test_sub_workflow_not_yet_in_the_root_db_is_not_shown_as_the_root(tmp_path):
    """Before monitord records the sub-workflow there is nothing to read; the
    root's rows under the sub-workflow's name would be worse than nothing."""
    _, sub_dir = _hierarchy(tmp_path, sub_recorded=False)
    assert read_stampede_events(sub_dir) == []
    assert read_stampede_job_details(sub_dir) == {}
    assert slow_steps(sub_dir, now=NOW) == []


def test_slow_steps_are_reported_under_the_workflow_they_belong_to(tmp_path):
    root_dir, sub_dir = _hierarchy(tmp_path)
    assert slow_steps(root_dir, now=NOW) == []
    [slow] = slow_steps(sub_dir, now=NOW)
    assert slow["job_id"] == "proc_inner_b"
    assert slow["running_seconds"] == 2400 and slow["usual_seconds"] == 10.0


def test_db_with_no_workflow_row_yet_still_reads_running(tmp_path):
    """monitord creates the tables before it writes the workflow: a submitted
    run in that window read "running" with no jobs, and still must."""
    run_dir = str(tmp_path)
    _braindump(run_dir, ROOT_UUID, ROOT_UUID, run_dir)
    con = sqlite3.connect(os.path.join(run_dir, "pm-root-0.stampede.db"))
    con.executescript(_SCHEMA)
    con.commit()
    con.close()
    events = read_stampede_events(run_dir)
    assert event_reader.workflow_status(events) == "running"
    assert event_reader.jobs(events) == []
    assert read_stampede_job_details(run_dir) == {}
