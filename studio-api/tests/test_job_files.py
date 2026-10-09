"""The graph's job panel: which files a job uses, and where each can be opened."""

from __future__ import annotations

import os

import yaml

from services import job_files

_WF = {
    "name": "earthquake",
    "jobs": [
        {
            "id": "ID0000001",
            "name": "fetch",
            "uses": [
                {"lfn": "regions.txt", "type": "input"},
                {"lfn": "catalog.csv", "type": "output", "stageOut": True},
            ],
        },
        {
            "id": "ID0000002",
            "name": "analyze",
            "uses": [
                {"lfn": "catalog.csv", "type": "input"},
                {"lfn": "data/zones.json", "type": "output", "stageOut": True},
                {"lfn": "tmp.bin", "type": "output", "stageOut": False},
            ],
        },
    ],
    "replicaCatalog": {
        "replicas": [{"lfn": "regions.txt", "pfns": [{"site": "local", "pfn": "PLACEHOLDER"}]}]
    },
}


def _project(tmp_path):
    """A project with a run whose scratch, output and inputs are laid out the
    way Pegasus lays them out (submit and scratch mirror each other)."""
    proj = tmp_path / "workflows" / "earthquake"
    run = proj / "submit" / "u" / "pegasus" / "earthquake" / "run0001"
    run.mkdir(parents=True)
    scratch = proj / "scratch" / "u" / "pegasus" / "earthquake" / "run0001"
    scratch.mkdir(parents=True)
    output = proj / "output"
    output.mkdir()
    (proj / "regions.txt").write_text("california\n")
    wf = dict(_WF)
    wf["replicaCatalog"] = {
        "replicas": [{"lfn": "regions.txt", "pfns": [{"site": "local", "pfn": f"file://{proj}/regions.txt"}]}]
    }
    (proj / "workflow.yml").write_text(yaml.safe_dump(wf))
    # pegasus-plan copies the workflow it planned into the run directory.
    (run / "workflow.yml").write_text(yaml.safe_dump(wf))
    (proj / "sites.yml").write_text(yaml.safe_dump({"sites": [{
        "name": "local",
        "directories": [
            {"type": "sharedScratch", "path": str(proj / "scratch")},
            {"type": "localStorage", "path": str(output)},
        ],
    }]}))
    (run / "braindump.yml").write_text(
        f"basedir: {proj / 'submit'}\nsubmit_dir: {run}\n"
    )
    return proj, run, scratch, output, wf


def test_uses_come_from_the_abstract_job_and_exec_ids_resolve():
    uses = job_files.job_uses(_WF, "ID0000002")
    assert [(u["lfn"], u["link"], u["stage_out"]) for u in uses] == [
        ("catalog.csv", "input", False),
        ("data/zones.json", "output", True),
        ("tmp.bin", "output", False),
    ]
    # The "Pegasus jobs" view uses executable ids: <transformation>_<id>.
    assert job_files.job_uses(_WF, "analyze_ID0000002") == uses
    # Pegasus's own jobs are not in the abstract workflow.
    assert job_files.job_uses(_WF, "stage_in_local_0_0") is None


def test_files_are_found_where_the_run_left_them(tmp_path):
    proj, run, scratch, output, wf = _project(tmp_path)
    (output / "catalog.csv").write_text("a,b\n")       # staged-out result
    (scratch / "catalog.csv").write_text("a,b\n")      # also in scratch
    (scratch / "data").mkdir()
    (scratch / "data" / "zones.json").write_text("{}")  # not staged out yet

    bases = job_files.bases_for_run(str(run), [str(tmp_path / "workflows")])
    assert bases["scratch"] == os.path.realpath(scratch)

    fetch = {f["lfn"]: f for f in job_files.describe(
        job_files.job_uses(wf, "ID0000001"), bases, wf)}
    # An output is shown from the results first: that copy outlives the run.
    assert fetch["catalog.csv"]["location"] == "result"
    assert fetch["catalog.csv"]["previewable"] is True
    # An input with a local replica in the project is previewable before staging.
    assert fetch["regions.txt"]["location"] == "project"
    assert fetch["regions.txt"]["path"] == "regions.txt"

    analyze = {f["lfn"]: f for f in job_files.describe(
        job_files.job_uses(wf, "ID0000002"), bases, wf)}
    assert analyze["catalog.csv"]["location"] == "scratch"      # input: scratch first
    # Once scratch is cleaned, an upstream output is still found in the results.
    (scratch / "catalog.csv").unlink()
    again = {f["lfn"]: f for f in job_files.describe(
        job_files.job_uses(wf, "ID0000002"), bases, wf)}
    assert again["catalog.csv"]["location"] == "result"
    assert analyze["data/zones.json"]["location"] == "scratch"  # not staged out yet
    assert analyze["data/zones.json"]["kind"] == "text"
    # Produced nowhere (yet): listed, but nothing to open.
    assert analyze["tmp.bin"]["location"] is None
    assert analyze["tmp.bin"]["previewable"] is False


def test_a_replica_outside_the_project_is_never_served(tmp_path):
    proj, run, scratch, output, wf = _project(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("no")
    wf["replicaCatalog"]["replicas"][0]["pfns"][0]["pfn"] = f"file://{outside}"
    bases = job_files.bases_for_run(str(run), [str(tmp_path / "workflows")])
    files = job_files.describe(job_files.job_uses(wf, "ID0000001"), bases, wf)
    regions = next(f for f in files if f["lfn"] == "regions.txt")
    assert regions["location"] is None


def test_endpoints_list_and_serve_with_path_confinement(client, monkeypatch, tmp_path):
    import routers.workflows as wf_router
    from services import workflow_scanner

    proj, run, scratch, output, wf = _project(tmp_path)
    (output / "catalog.csv").write_text("a,b\n1,2\n")
    root = str(tmp_path / "workflows")

    async def _resolve(run_id):
        return str(run)

    monkeypatch.setattr(wf_router, "_resolve_run_dir", _resolve)
    monkeypatch.setattr(workflow_scanner, "project_roots", lambda: [root])
    monkeypatch.setattr(wf_router, "WORKSPACE_ROOT", str(tmp_path))

    listing = client.get("/api/workflows/r1/jobs/ID0000001/files").json()
    assert listing["job_found"] is True
    by = {f["lfn"]: f for f in listing["files"]}
    assert by["catalog.csv"]["location"] == "result"

    ok = client.get(
        "/api/workflows/r1/files/content",
        params={"location": "result", "path": "catalog.csv"},
    )
    assert ok.status_code == 200 and ok.text.startswith("a,b")

    escape = client.get(
        "/api/workflows/r1/files/content",
        params={"location": "result", "path": "../regions.txt"},
    )
    assert escape.status_code == 403

    aux = client.get("/api/workflows/r1/jobs/stage_in_local_0_0/files").json()
    assert aux["job_found"] is False and aux["files"] == []


def test_images_get_a_larger_preview_ceiling_than_text(tmp_path):
    from services import run_outputs

    big = 3 * 1024 * 1024  # earthquake's plots are ~3 MB
    (tmp_path / "plot.png").write_bytes(b"\0" * big)
    (tmp_path / "data.json").write_text("x" * big)
    assert run_outputs._entry(str(tmp_path / "plot.png"), "plot.png")["previewable"] is True
    assert run_outputs._entry(str(tmp_path / "data.json"), "data.json")["previewable"] is False
