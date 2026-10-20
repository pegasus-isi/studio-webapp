"""services/slow_steps: running steps well past their usual time (#586)."""

from __future__ import annotations

import os
import sqlite3

from services.slow_steps import slow_steps

NOW = 1_000_000.0


def _run(parent, name, finished=(), running=()):
    """A run dir with a stampede DB in the real schema's shape.
    finished: (kind, duration, exitcode); running: (kind, seconds_ago)."""
    run_dir = os.path.join(parent, name)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "braindump.yml"), "w") as f:
        f.write(f"submit_dir: {run_dir}\n")
    con = sqlite3.connect(os.path.join(run_dir, "wf-0.stampede.db"))
    con.executescript(
        """
        CREATE TABLE job (job_id INTEGER, exec_job_id TEXT, type_desc TEXT);
        CREATE TABLE task (job_id INTEGER, transformation TEXT);
        CREATE TABLE job_instance (job_instance_id INTEGER, job_id INTEGER,
            job_submit_seq INTEGER, local_duration REAL, exitcode INTEGER);
        CREATE TABLE jobstate (job_instance_id INTEGER, state TEXT,
            timestamp REAL, jobstate_submit_seq INTEGER);
        """
    )
    n = 0
    for kind, duration, code in finished:
        n += 1
        con.execute("INSERT INTO job VALUES (?,?,?)", (n, f"{kind}_{n}", "compute"))
        con.execute("INSERT INTO task VALUES (?,?)", (n, kind))
        con.execute("INSERT INTO job_instance VALUES (?,?,1,?,?)", (n, n, duration, code))
        con.execute("INSERT INTO jobstate VALUES (?,'EXECUTE',?,1)", (n, NOW - 9999))
        con.execute("INSERT INTO jobstate VALUES (?,'JOB_SUCCESS',?,2)", (n, NOW - 9000))
    for kind, ago in running:
        n += 1
        con.execute("INSERT INTO job VALUES (?,?,?)", (n, f"{kind}_{n}", "compute"))
        con.execute("INSERT INTO task VALUES (?,?)", (n, kind))
        con.execute("INSERT INTO job_instance VALUES (?,?,1,NULL,NULL)", (n, n))
        con.execute("INSERT INTO jobstate VALUES (?,'SUBMIT',?,1)", (n, NOW - ago - 30))
        con.execute("INSERT INTO jobstate VALUES (?,'EXECUTE',?,2)", (n, NOW - ago))
    con.commit()
    con.close()
    return run_dir


def test_flags_the_expanse_case_using_an_earlier_run(tmp_path):
    """fetch_data took 18 s in run0006 and has run 40 min in run0012."""
    _run(str(tmp_path), "run0006", finished=[("fetch_data", 18.0, 0)])
    current = _run(str(tmp_path), "run0012", running=[("fetch_data", 2400)])
    [s] = slow_steps(current, now=NOW)
    assert s["transformation"] == "fetch_data"
    assert s["running_seconds"] == 2400 and s["usual_seconds"] == 18.0
    assert s["basis"] == "earlier runs" and s["samples"] == 1


def test_prefers_this_runs_own_history(tmp_path):
    _run(str(tmp_path), "run0001", finished=[("smili", 5.0, 0)])
    current = _run(str(tmp_path), "run0002", finished=[("smili", 200.0, 0)], running=[("smili", 1200)])
    [s] = slow_steps(current, now=NOW)
    assert s["basis"] == "this run" and s["usual_seconds"] == 200.0


def test_never_flags_without_history(tmp_path):
    current = _run(str(tmp_path), "run0001", running=[("new_step", 99_999)])
    assert slow_steps(current, now=NOW) == []


def test_needs_both_the_floor_and_the_factor(tmp_path):
    # 5x usual but under 10 minutes
    a = _run(str(tmp_path / "a"), "run0001", finished=[("x", 60.0, 0)], running=[("x", 400)])
    assert slow_steps(a, now=NOW) == []
    # over 10 minutes but under 5x a long usual time
    b = _run(str(tmp_path / "b"), "run0001", finished=[("y", 3600.0, 0)], running=[("y", 7200)])
    assert slow_steps(b, now=NOW) == []


def test_ignores_failed_samples_and_finished_steps(tmp_path):
    # the only sample failed: no usable history
    current = _run(str(tmp_path), "run0001", finished=[("z", 1.0, 1)], running=[("z", 5000)])
    assert slow_steps(current, now=NOW) == []


def test_no_database_is_no_slow_steps(tmp_path):
    assert slow_steps(str(tmp_path / "nothing"), now=NOW) == []


def _submitted_only(run_dir, kind, n=99):
    """A job monitord has only seen SUBMIT for (the Expanse hang)."""
    con = sqlite3.connect(os.path.join(run_dir, "wf-0.stampede.db"))
    con.execute("INSERT INTO job VALUES (?,?,?)", (n, f"{kind}_{kind}", "compute"))
    con.execute("INSERT INTO task VALUES (?,?)", (n, kind))
    con.execute("INSERT INTO job_instance VALUES (?,?,1,NULL,NULL)", (n, n))
    con.execute("INSERT INTO jobstate VALUES (?,'SUBMIT',?,1)", (n, NOW - 2500))
    con.commit()
    con.close()


def test_the_scheduler_catches_a_slot_held_without_executing(tmp_path):
    """Exactly the Expanse case: monitord still at SUBMIT, condor_q says the
    job has held a slot on exp-1-01 for 40 minutes; it took 18 s before."""
    _run(str(tmp_path), "run0006", finished=[("fetch_data", 18.0, 0)])
    current = _run(str(tmp_path), "run0012")
    _submitted_only(current, "fetch_data")
    queue = [{"DAGNodeName": "fetch_data_fetch_data", "JobCurrentStartDate": NOW - 2400,
              "RemoteHost": "slot1_1@exp-1-01.expanse.sdsc.edu"}]
    [s] = slow_steps(current, now=NOW, queue=queue)
    assert s["running_seconds"] == 2400 and s["host"] == "exp-1-01.expanse.sdsc.edu"
    assert s["started"] is False
    # Without the scheduler's view, monitord alone would not have seen it.
    assert slow_steps(current, now=NOW, queue=None) == []


def test_scheduler_view_marks_a_genuinely_slow_step_as_started(tmp_path):
    current = _run(str(tmp_path), "run0001", finished=[("x", 30.0, 0)], running=[("x", 1000)])
    queue = [{"DAGNodeName": "x_2", "JobCurrentStartDate": NOW - 1000, "RemoteHost": "slot1@ep-1"}]
    [s] = slow_steps(current, now=NOW, queue=queue)
    assert s["started"] is True and s["host"] == "ep-1"


def test_an_empty_queue_means_nothing_is_running(tmp_path):
    current = _run(str(tmp_path), "run0001", finished=[("x", 30.0, 0)], running=[("x", 9999)])
    assert slow_steps(current, now=NOW, queue=[]) == []


def test_wf_uuid_is_read_from_the_braindump(tmp_path):
    from services.slow_steps import queue_query, wf_uuid

    d = tmp_path / "run"
    d.mkdir()
    (d / "braindump.yml").write_text('root_wf_uuid: "9e2a130b-d562-4ec8-af32-b86db31a157f"\nwf_uuid: "9e2a130b-d562-4ec8-af32-b86db31a157f"\n')
    u = wf_uuid(str(d))
    assert u == "9e2a130b-d562-4ec8-af32-b86db31a157f"
    assert f'pegasus_wf_uuid == "{u}"' in queue_query(u)[3]
    (d / "braindump.yml").write_text('wf_uuid: "x\\" || true"\n')
    assert wf_uuid(str(d)) is None
