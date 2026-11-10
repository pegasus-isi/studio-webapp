"""Steps that have been running much longer than they usually take (#586).

In the Expanse annex test a `fetch_data` step sat on a node for 40 minutes;
the same step had taken 18 seconds in an earlier run. Nothing on the run
page said so: Pegasus only returns a step's logs once it finishes, and
reading a running step's output needs a connection only the access point
has. What Studio does have, live, is pegasus-monitord's job states in the
run's stampede DB, including when each step started executing, and the
durations of the same steps in this run and in earlier runs of the same
workflow, whose DBs sit beside it. That is enough to say "this has been
running 40 minutes; it usually takes about 20 seconds", which is the
signal that was missing.

A step is flagged only when it has run at least MIN_SECONDS *and* at least
FACTOR times its usual time; a step with no history is never flagged, so a
workflow's first run raises no false alarms.

"Running since" comes from the scheduler when it can be asked: each of
Pegasus's jobs carries +pegasus_wf_uuid, and condor_q gives the slot's
JobCurrentStartDate and RemoteHost. monitord's EXECUTE event is only the
fallback, because in the Expanse case it never arrived: the job held a slot
on the stuck node for 40 minutes with monitord still showing SUBMIT, then
JOB_EVICTED, and EXECUTE only on the next node. A job the scheduler says is
running but monitord never saw execute is reported as "not started".
"""

from __future__ import annotations

import os
import re
import sqlite3
import statistics
import time
from typing import Any

import structlog

from services.stampede_reader import open_stampede

log = structlog.get_logger()

MIN_SECONDS = 600
FACTOR = 5.0
EARLIER_RUNS = 5

_RUN = re.compile(r"^run\d+$")


def _connect(run_dir: str) -> tuple[sqlite3.Connection, str, tuple[Any, ...]] | None:
    """(connection, SQL restricting `job j` to this run's workflow, its args).

    A hierarchical run keeps the root and its sub-workflows in one DB, and
    exec job ids repeat across them (each has a register_local_0_0), so
    every query here is scoped to the run directory's own workflow. A DB
    with no workflow table has nothing to scope by and is read whole.
    """
    opened = open_stampede(run_dir)
    if opened is None:
        return None
    con, wf_id = opened
    if wf_id is None:
        return con, "", ()
    return con, " WHERE j.wf_id = ?", (wf_id,)


def _key(row: sqlite3.Row) -> str:
    """Group by transformation; Pegasus's own jobs (no task) by job type."""
    return str(row["transformation"] or row["type_desc"] or "")


def _durations(run_dir: str) -> dict[str, list[float]]:
    """Successful durations per step kind in one run."""
    opened = _connect(run_dir)
    if opened is None:
        return {}
    con, where, args = opened
    out: dict[str, list[float]] = {}
    try:
        for r in con.execute(
            "SELECT t.transformation, j.type_desc, ji.local_duration, ji.exitcode "
            "FROM job_instance ji JOIN job j ON ji.job_id = j.job_id "
            "LEFT JOIN task t ON t.job_id = j.job_id" + where,
            args,
        ):
            d = r["local_duration"]
            if r["exitcode"] == 0 and isinstance(d, (int, float)) and d > 0:
                out.setdefault(_key(r), []).append(float(d))
    except sqlite3.Error as e:
        log.debug("slow_steps_durations_unreadable", run_dir=run_dir, error=str(e))
    finally:
        con.close()
    return out


def _earlier_runs(run_dir: str) -> list[str]:
    """Up to EARLIER_RUNS sibling runs before this one, newest first."""
    parent, name = os.path.split(os.path.normpath(run_dir))
    try:
        siblings = sorted(n for n in os.listdir(parent) if _RUN.match(n) and n < name)
    except OSError:
        return []
    return [os.path.join(parent, n) for n in reversed(siblings[-EARLIER_RUNS:])]


def _kinds(
    con: sqlite3.Connection, where: str = "", args: tuple[Any, ...] = ()
) -> dict[str, tuple[str, str]]:
    """exec_job_id -> (kind, latest state) for every job in the run."""
    out: dict[str, tuple[str, str]] = {}
    for r in con.execute(
        "SELECT j.exec_job_id, j.type_desc, t.transformation, js.state "
        "FROM job j "
        "LEFT JOIN job_instance ji ON ji.job_id = j.job_id "
        "AND ji.job_submit_seq = (SELECT MAX(job_submit_seq) FROM job_instance WHERE job_id = j.job_id) "
        "LEFT JOIN jobstate js ON js.job_instance_id = ji.job_instance_id "
        "AND js.jobstate_submit_seq = (SELECT MAX(jobstate_submit_seq) FROM jobstate "
        "WHERE job_instance_id = ji.job_instance_id) "
        "LEFT JOIN task t ON t.job_id = j.job_id" + where,
        args,
    ):
        out[str(r["exec_job_id"])] = (_key(r), str(r["state"] or ""))
    return out


def _running(
    con: sqlite3.Connection, where: str = "", args: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    """Steps whose latest instance's latest state is EXECUTE, with its time."""
    rows = con.execute(
        "SELECT j.exec_job_id, j.type_desc, t.transformation, js.state, js.timestamp "
        "FROM job j "
        "JOIN job_instance ji ON ji.job_id = j.job_id "
        "AND ji.job_submit_seq = (SELECT MAX(job_submit_seq) FROM job_instance WHERE job_id = j.job_id) "
        "JOIN jobstate js ON js.job_instance_id = ji.job_instance_id "
        "AND js.jobstate_submit_seq = (SELECT MAX(jobstate_submit_seq) FROM jobstate "
        "WHERE job_instance_id = ji.job_instance_id) "
        "LEFT JOIN task t ON t.job_id = j.job_id" + where,
        args,
    ).fetchall()
    return [
        {"job_id": r["exec_job_id"], "kind": _key(r), "since": float(r["timestamp"])}
        for r in rows
        if r["state"] == "EXECUTE" and isinstance(r["timestamp"], (int, float))
    ]


def wf_uuid(run_dir: str) -> str | None:
    """The run's workflow id from braindump.yml, or None."""
    try:
        with open(os.path.join(run_dir, "braindump.yml"), encoding="utf-8") as fh:
            for line in fh:
                key, _, value = line.partition(":")
                if key.strip() == "wf_uuid":
                    v = value.strip().strip('"').strip("'")
                    return v if re.fullmatch(r"[0-9a-fA-F-]{8,64}", v) else None
    except OSError:
        return None
    return None


def queue_query(uuid: str) -> list[str]:
    """condor_q for this run's jobs that hold a slot right now."""
    return [
        "condor_q", "-json",
        "-constraint", f'pegasus_wf_uuid == "{uuid}" && JobStatus == 2',
        "-attributes", "DAGNodeName,JobCurrentStartDate,RemoteHost",
    ]


def slow_steps(
    run_dir: str,
    now: float | None = None,
    queue: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Running steps that are well past their usual time, slowest first.

    ``queue`` is condor_q's rows for this run (see queue_query); None means
    the scheduler could not be asked, and monitord's EXECUTE times are used.
    """
    opened = _connect(run_dir)
    if opened is None:
        return []
    con, where, args = opened
    try:
        kinds = _kinds(con, where, args)
        if queue is None:
            running = _running(con, where, args)
        else:
            running = []
            for row in queue:
                row = {k.lower(): v for k, v in row.items()}
                node = str(row.get("dagnodename") or "")
                start = row.get("jobcurrentstartdate")
                if node not in kinds or not isinstance(start, (int, float)):
                    continue
                kind, state = kinds[node]
                host = str(row.get("remotehost") or "")
                running.append({
                    "job_id": node,
                    "kind": kind,
                    "since": float(start),
                    "host": host.split("@", 1)[-1] or None,
                    "started": state not in ("SUBMIT", "", "JOB_EVICTED"),
                })
    except sqlite3.Error as e:
        log.debug("slow_steps_unreadable", run_dir=run_dir, error=str(e))
        return []
    finally:
        con.close()
    if not running:
        return []

    now = time.time() if now is None else now
    here = _durations(run_dir)
    earlier: dict[str, list[float]] = {}
    for d in _earlier_runs(run_dir):
        for k, v in _durations(d).items():
            earlier.setdefault(k, []).extend(v)

    out = []
    for step in running:
        samples = here.get(step["kind"]) or earlier.get(step["kind"])
        if not samples:
            continue  # no history: never guess
        usual = statistics.median(samples)
        elapsed = max(0.0, now - step["since"])
        if elapsed >= MIN_SECONDS and elapsed >= FACTOR * usual:
            out.append({
                "job_id": step["job_id"],
                "transformation": step["kind"],
                "running_seconds": round(elapsed),
                "usual_seconds": round(usual, 1),
                "basis": "this run" if here.get(step["kind"]) else "earlier runs",
                "samples": len(samples),
                "host": step.get("host"),
                # Holds a slot but monitord never saw it execute (the
                # Expanse case): likely stuck before the tool even ran.
                "started": step.get("started", True),
            })
    return sorted(out, key=lambda s: s["running_seconds"], reverse=True)
