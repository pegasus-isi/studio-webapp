"""Re-run from a step (#680): services/rerun.py.

The fixtures mirror the climate workflow the feature was measured on
(2026-11-13, Pegasus 6.0.0.dev1): prep_data -> analyze_station x3 ->
aggregate, where prep_data's outputs are intermediates (not staged out) and
every analyze output is staged to output/.
"""

from __future__ import annotations

import os
import sqlite3
import time

import pytest
import yaml

from services import rerun

STATIONS = ("A", "B", "C")


def _workflow() -> dict:
    jobs = [
        {
            "type": "job", "id": "prep_data", "name": "prep_data",
            "uses": [{"lfn": "raw.csv.gz", "type": "input"}]
            + [
                {"lfn": f"station_{s}.csv", "type": "output", "stageOut": False}
                for s in STATIONS
            ],
        }
    ]
    for s in STATIONS:
        jobs.append({
            "type": "job", "id": f"analyze_{s}", "name": "analyze_station",
            "uses": [
                {"lfn": f"station_{s}.csv", "type": "input"},
                {"lfn": f"{s}_trends.csv", "type": "output", "stageOut": True},
                {"lfn": f"{s}_anomalies.png", "type": "output", "stageOut": True},
            ],
        })
    jobs.append({
        "type": "job", "id": "aggregate", "name": "aggregate",
        "uses": [{"lfn": f"{s}_trends.csv", "type": "input"} for s in STATIONS]
        + [{"lfn": f"{s}_anomalies.png", "type": "input"} for s in STATIONS]
        + [{"lfn": "summary.pdf", "type": "output", "stageOut": True}],
    })
    return {
        "pegasus": "5.0.4",
        "name": "climate",
        "jobs": jobs,
        "jobDependencies": [
            {"id": "prep_data", "children": [f"analyze_{s}" for s in STATIONS]},
            *({"id": f"analyze_{s}", "children": ["aggregate"]} for s in STATIONS),
        ],
    }


def _stampede(run_dir: str, results: dict[str, tuple[int, float]]) -> None:
    """results: abstract job id -> (exitcode, submit time)."""
    con = sqlite3.connect(os.path.join(run_dir, "climate-0.stampede.db"))
    con.executescript(
        """
        CREATE TABLE task (task_id INTEGER, job_id INTEGER, abs_task_id TEXT);
        CREATE TABLE job_instance (job_instance_id INTEGER, job_id INTEGER,
            job_submit_seq INTEGER, exitcode INTEGER);
        CREATE TABLE jobstate (job_instance_id INTEGER, state TEXT, timestamp REAL);
        """
    )
    for n, (jid, (code, started)) in enumerate(results.items(), start=1):
        con.execute("INSERT INTO task VALUES (?,?,?)", (n, n, jid))
        con.execute("INSERT INTO job_instance VALUES (?,?,?,?)", (n, n, 1, code))
        con.execute("INSERT INTO jobstate VALUES (?,?,?)", (n, "SUBMIT", started))
    con.commit()
    con.close()


def _run(project: str, name: str, results: dict[str, tuple[int, float]], planned: float) -> str:
    run_dir = os.path.join(project, "submit", "u", "pegasus", "climate", name)
    os.makedirs(run_dir)
    out = os.path.join(project, "output")
    stamp = time.strftime("%Y%m%dT%H%M%S+0000", time.gmtime(planned))
    with open(os.path.join(run_dir, "braindump.yml"), "w") as f:
        f.write(
            f'dax: "{project}/workflow.yml"\n'
            f'submit_dir: "{run_dir}"\n'
            f'timestamp: "{stamp}"\n'
            f'planner_arguments: "\\"--dir submit -s compute -o local '
            f'--output-dir {out} --submit workflow.yml \\""\n'
        )
    with open(os.path.join(run_dir, "workflow.yml"), "w") as f:
        yaml.safe_dump(_workflow(), f)
    _stampede(run_dir, results)
    return run_dir


def _output(project: str, lfn: str, mtime: float) -> None:
    path = os.path.join(project, "output", lfn)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("x")
    os.utime(path, (mtime, mtime))


@pytest.fixture()
def project(tmp_path):
    p = str(tmp_path / "climate")
    os.makedirs(p)
    with open(os.path.join(p, "workflow.yml"), "w") as f:
        yaml.safe_dump(_workflow(), f)
    return p


def _full_run(project: str, name: str = "run0001", t0: float = 1_000_000.0) -> None:
    ok = {"prep_data": (0, t0)}
    ok.update({f"analyze_{s}": (0, t0 + 100) for s in STATIONS})
    ok["aggregate"] = (0, t0 + 200)
    _run(project, name, ok, planned=t0 - 10)
    for s in STATIONS:
        _output(project, f"{s}_trends.csv", t0 + 150)
        _output(project, f"{s}_anomalies.png", t0 + 150)
    _output(project, "summary.pdf", t0 + 250)


def _names(groups):
    return {g["name"]: g["count"] for g in groups}


def test_steps_are_grouped_by_name_upstream_first(project):
    jobs = rerun.load_workflow(os.path.join(project, "workflow.yml"))
    assert rerun.steps(jobs) == [
        {"name": "prep_data", "count": 1},
        {"name": "analyze_station", "count": 3},
        {"name": "aggregate", "count": 1},
    ]


def test_rerunning_the_last_step_reuses_everything_before_it(project):
    # The reported case: only the plot at the end changed.
    _full_run(project)
    plan = rerun.prepare(project, "aggregate", os.path.join(project, "workflow.yml"))
    s = plan.summary()
    assert _names(s["run"]) == {"aggregate": 1}
    assert _names(s["reused"]) == {"prep_data": 1, "analyze_station": 3}
    assert s["missing"] == []
    assert s["sources"] == ["run0001"]
    # The chosen step's own output is never offered for reuse.
    assert "summary.pdf" not in {e.lfn for e in plan.entries}
    assert len(plan.entries) == 6


def test_intermediates_nobody_kept_rerun_their_producer(project):
    # station_*.csv were never staged out, and cleanup deleted them.
    _full_run(project)
    plan = rerun.prepare(project, "analyze_station", os.path.join(project, "workflow.yml"))
    s = plan.summary()
    assert _names(s["run"]) == {"prep_data": 1, "analyze_station": 3, "aggregate": 1}
    assert {m["file"] for m in s["missing"]} == {f"station_{x}.csv" for x in STATIONS}
    assert {m["step"] for m in s["missing"]} == {"prep_data"}


def test_one_missing_input_reruns_only_its_branch(project):
    # Measured: with one PNG gone, Pegasus planned aggregate, that station's
    # analyze and prep_data, and reused the other two analyze jobs.
    _full_run(project)
    os.remove(os.path.join(project, "output", "B_anomalies.png"))
    plan = rerun.prepare(project, "aggregate", os.path.join(project, "workflow.yml"))
    assert set(plan.run) == {"prep_data", "analyze_B", "aggregate"}
    assert set(plan.reused) == {"analyze_A", "analyze_C"}
    assert plan.summary()["missing"] == [{"file": "B_anomalies.png", "step": "analyze_station"}]


def test_a_file_older_than_its_producer_is_not_reused(project):
    # The newest run's analyze_A succeeded but its stage-out never replaced
    # the file: what's on disk is the older run's.
    _full_run(project, "run0001", t0=1_000_000.0)
    ok = {"prep_data": (0, 2_000_000.0)}
    ok.update({f"analyze_{s}": (0, 2_000_100.0) for s in STATIONS})
    _run(project, "run0002", ok, planned=1_999_990.0)
    for s in ("B", "C"):
        _output(project, f"{s}_trends.csv", 2_000_150.0)
        _output(project, f"{s}_anomalies.png", 2_000_150.0)
    plan = rerun.prepare(project, "aggregate", os.path.join(project, "workflow.yml"))
    listed = {e.lfn for e in plan.entries}
    assert "A_trends.csv" not in listed and "A_anomalies.png" not in listed
    assert {e.run_name for e in plan.entries} == {"run0002"}
    assert "analyze_A" in plan.run


def test_a_failed_job_does_not_claim_its_outputs(project):
    # run0002 re-ran analyze_A and it failed: run0001's file is still current.
    _full_run(project, "run0001", t0=1_000_000.0)
    _run(project, "run0002", {"analyze_A": (1, 2_000_000.0)}, planned=1_999_990.0)
    plan = rerun.prepare(project, "aggregate", os.path.join(project, "workflow.yml"))
    assert "A_trends.csv" in {e.lfn for e in plan.entries}
    assert plan.run == ["aggregate"]


def test_a_partial_rerun_keeps_upstream_files_current(project):
    # After re-running only aggregate, the analyze files from run0001 are
    # still the current ones: a run that didn't run a job doesn't claim it.
    _full_run(project, "run0001", t0=1_000_000.0)
    _run(project, "run0002", {"aggregate": (0, 2_000_000.0)}, planned=1_999_990.0)
    _output(project, "summary.pdf", 2_000_050.0)
    plan = rerun.prepare(project, "aggregate", os.path.join(project, "workflow.yml"))
    assert plan.run == ["aggregate"]
    assert {e.run_name for e in plan.entries} == {"run0001"}


def test_no_earlier_runs_means_everything_runs(project):
    plan = rerun.prepare(project, "aggregate", os.path.join(project, "workflow.yml"))
    assert plan.entries == []
    assert len(plan.run) == 5


def test_unknown_step_names_the_real_ones(project):
    with pytest.raises(rerun.RerunError, match="prep_data, analyze_station, aggregate"):
        rerun.prepare(project, "plot", os.path.join(project, "workflow.yml"))


def test_subworkflows_are_refused_rather_than_misjudged(project):
    wf = _workflow()
    wf["jobs"].append({"type": "pegasusWorkflow", "id": "sub", "name": "sub", "file": "x.yml", "uses": []})
    path = os.path.join(project, "workflow.yml")
    with open(path, "w") as f:
        yaml.safe_dump(wf, f)
    with pytest.raises(rerun.RerunError, match="sub-workflows"):
        rerun.load_workflow(path)


def test_replica_file_is_quoted_and_on_the_local_site(project):
    _full_run(project)
    plan = rerun.prepare(project, "aggregate", os.path.join(project, "workflow.yml"))
    path = rerun.write_rc(project, plan)
    assert path == os.path.join(project, ".studio", "reuse", "rerun.rc")
    lines = [l for l in open(path).read().splitlines() if not l.startswith("#")]
    out = os.path.join(project, "output")
    assert f'"A_trends.csv" "file://{out}/A_trends.csv" site="local"' in lines
    assert len(lines) == 6


def test_planned_steps_reads_compute_jobs_from_the_dag(project, tmp_path):
    jobs = rerun.load_workflow(os.path.join(project, "workflow.yml"))
    run_dir = tmp_path / "planned"
    run_dir.mkdir()
    (run_dir / "climate-0.dag").write_text(
        "JOB create_dir_climate_0_local x.sub\n"
        "JOB stage_in_local_local_0_0 y.sub\n"
        "JOB aggregate_aggregate z.sub\n"
        "JOB stage_out_local_local_0_0 w.sub\n"
        "JOB cleanup_climate_0_local v.sub\n"
    )
    assert rerun.planned_steps(str(run_dir), jobs) == [{"name": "aggregate", "count": 1}]
    assert rerun.planned_steps(str(tmp_path / "nowhere"), jobs) is None


# ── API ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def api_project(project, monkeypatch):
    import routers.workflows as wf

    async def projects():
        return [{"project_id": "climate", "project_dir": project}]

    monkeypatch.setattr(wf, "discover_projects_async", projects)
    return project


def test_steps_endpoint_lists_the_workflow_steps(client, api_project):
    r = client.get("/api/workflows/projects/climate/steps")
    assert r.status_code == 200
    assert [s["name"] for s in r.json()["steps"]] == ["prep_data", "analyze_station", "aggregate"]


def test_preview_endpoint_estimates_without_writing(client, api_project):
    _full_run(api_project)
    r = client.get("/api/workflows/projects/climate/rerun-preview", params={"from_step": "aggregate"})
    assert r.status_code == 200
    assert r.json()["run"] == [{"name": "aggregate", "count": 1}]
    assert not os.path.exists(os.path.join(api_project, ".studio", "reuse"))
    bad = client.get("/api/workflows/projects/climate/rerun-preview", params={"from_step": "nope"})
    assert bad.status_code == 400


def test_steps_endpoint_404s_before_generate(client, api_project):
    os.remove(os.path.join(api_project, "workflow.yml"))
    assert client.get("/api/workflows/projects/climate/steps").status_code == 404


def test_planned_steps_prefers_the_longest_matching_id(tmp_path):
    wf = {
        "pegasus": "5.0",
        "jobs": [
            {"type": "job", "id": "A", "name": "fetch", "uses": []},
            {"type": "job", "id": "analyze_A", "name": "analyze_station", "uses": []},
        ],
    }
    path = tmp_path / "wf.yml"
    path.write_text(yaml.safe_dump(wf))
    jobs = rerun.load_workflow(str(path))
    (tmp_path / "x.dag").write_text("JOB analyze_station_analyze_A a.sub\n")
    assert rerun.planned_steps(str(tmp_path), jobs) == [{"name": "analyze_station", "count": 1}]
