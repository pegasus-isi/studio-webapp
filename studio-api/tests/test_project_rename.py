"""Renaming a project folder from the Analyses page."""

from __future__ import annotations

import os

import pytest

from services import project_rename as pr


def _project(root, name="earthquake", runs=()):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    return {
        "project_id": name,
        "project_dir": str(d),
        "submitted_runs": [dict(r) for r in runs],
    }


def test_moves_the_folder_and_repoints_its_catalogs(tmp_path):
    root = tmp_path / "workflows"
    proj = _project(root)
    old = proj["project_dir"]
    (root / "earthquake" / "sites.yml").write_text(
        f"path: {old}/scratch\nurl: file://{old}/output\n"
        # A sibling whose name starts the same must not be touched.
        f"other: {old}2/scratch\n"
    )
    (root / "earthquake" / "pegasus.properties").write_text(
        f"pegasus.catalog.site.file = {old}/sites.yml\n"
    )
    (root / "earthquake" / "notes.txt").write_text(f"{old}/scratch\n")

    out = pr.rename_project_dir(proj, "earthquake-ca", [str(root)])

    new = str(root / "earthquake-ca")
    assert out["new_dir"] == new
    assert not os.path.exists(old) and os.path.isdir(new)
    sites = (root / "earthquake-ca" / "sites.yml").read_text()
    assert f"path: {new}/scratch" in sites
    assert f"url: file://{new}/output" in sites
    assert f"other: {old}2/scratch" in sites
    assert (root / "earthquake-ca" / "pegasus.properties").read_text() == (
        f"pegasus.catalog.site.file = {new}/sites.yml\n"
    )
    # Only Pegasus catalogs are rewritten, never the user's own files.
    assert (root / "earthquake-ca" / "notes.txt").read_text() == f"{old}/scratch\n"
    assert sorted(out["catalogs_updated"]) == ["pegasus.properties", "sites.yml"]


@pytest.mark.parametrize("status", ["running", "planning"])
def test_refuses_while_a_run_holds_the_paths(tmp_path, status):
    root = tmp_path / "workflows"
    proj = _project(root, runs=[{"run_name": "run0002", "status": status}])
    with pytest.raises(pr.RenameError) as e:
        pr.rename_project_dir(proj, "renamed", [str(root)])
    assert e.value.status == 409 and "run0002" in e.value.detail
    assert os.path.isdir(proj["project_dir"])


def test_finished_and_planned_runs_do_not_block(tmp_path):
    root = tmp_path / "workflows"
    proj = _project(root, runs=[
        {"status": "succeeded"}, {"status": "failed"}, {"status": "planned"},
    ])
    pr.rename_project_dir(proj, "renamed", [str(root)])
    assert (root / "renamed").is_dir()


def test_refuses_an_existing_name(tmp_path):
    root = tmp_path / "workflows"
    proj = _project(root)
    (root / "taken").mkdir()
    with pytest.raises(pr.RenameError) as e:
        pr.rename_project_dir(proj, "taken", [str(root)])
    assert e.value.status == 409


@pytest.mark.parametrize(
    "name", ["", " ", ".hidden", "..", "a/b", "has space", "-flag", "x" * 101]
)
def test_rejects_unusable_names(tmp_path, name):
    root = tmp_path / "workflows"
    proj = _project(root)
    with pytest.raises(pr.RenameError) as e:
        pr.rename_project_dir(proj, name, [str(root)])
    assert e.value.status == 400


def test_refuses_outside_the_workflow_roots(tmp_path):
    proj = _project(tmp_path / "elsewhere")
    with pytest.raises(pr.RenameError) as e:
        pr.rename_project_dir(proj, "renamed", [str(tmp_path / "workflows")])
    assert e.value.status == 403


def test_stored_run_paths_follow_the_move(tmp_path):
    """Run history and feedback episodes are keyed by run_dir; left on the old
    path they would show as vanished runs beside the same runs rescanned."""
    import asyncio

    from db import Database

    old = "/home/u/workflows/stream_flow"  # `_` is a LIKE wildcard: must not matter
    new = "/home/u/workflows/streamflow-v2"

    async def go():
        db = Database(str(tmp_path / "r.db"))
        await db.connect()
        try:
            for run_id, run_dir in (
                ("r1", f"{old}/submit/run0001"),
                ("r2", f"{old}"),
                ("r3", "/home/u/workflows/streamXflow/submit/run0001"),
                ("r4", f"{old}2/submit/run0001"),
            ):
                await db.upsert_workflow_run(
                    run_id=run_id, name="streamflow-0", run_dir=run_dir,
                    status="succeeded", total_jobs=1, completed_jobs=1,
                    failed_jobs=0,
                )
            changed = await db.move_run_dirs(old, new)
            rows = {r["run_id"]: r["run_dir"] for r in await db.list_workflow_runs()}
            return changed, rows
        finally:
            await db.close()

    changed, rows = asyncio.run(go())
    assert changed == 2
    assert rows["r1"] == f"{new}/submit/run0001"
    assert rows["r2"] == new
    assert rows["r3"] == "/home/u/workflows/streamXflow/submit/run0001"
    assert rows["r4"] == f"{old}2/submit/run0001"


def test_move_also_repoints_episode_rows_when_the_table_exists(tmp_path):
    """The feedback-episodes feature is not in this branch, but move_run_dirs
    must handle its table when present (e.g. a future cherry-pick adds it)."""
    import asyncio

    from db import Database

    old = "/home/u/workflows/streamflow"
    new = "/home/u/workflows/streamflow-v2"

    async def go():
        db = Database(str(tmp_path / "r.db"))
        await db.connect()
        try:
            await db.db.execute(
                "CREATE TABLE feedback_episodes "
                "(id INTEGER PRIMARY KEY, run_dir TEXT, rerun_dir TEXT)"
            )
            await db.db.execute(
                "INSERT INTO feedback_episodes (run_dir, rerun_dir) VALUES (?, ?)",
                (f"{old}/submit/run0001", f"{old}/submit/rerun0001"),
            )
            await db.db.commit()
            changed = await db.move_run_dirs(old, new)
            cur = await db.db.execute("SELECT run_dir, rerun_dir FROM feedback_episodes")
            return changed, await cur.fetchone()
        finally:
            await db.close()

    changed, row = asyncio.run(go())
    assert changed == 2
    assert tuple(row) == (f"{new}/submit/run0001", f"{new}/submit/rerun0001")


def test_rename_endpoint(client, monkeypatch, tmp_path):
    import routers.workflows as wf
    from services import workflow_scanner

    root = tmp_path / "workflows"
    proj = _project(root, name="streamflow")

    async def _projects(ws=None):
        return [proj] if os.path.isdir(proj["project_dir"]) else []

    monkeypatch.setattr(wf, "discover_projects_async", _projects)
    monkeypatch.setattr(workflow_scanner, "project_roots", lambda: [str(root)])

    bad = client.post(
        "/api/workflows/projects/streamflow/rename", json={"name": "has space"}
    )
    assert bad.status_code == 400

    resp = client.post(
        "/api/workflows/projects/streamflow/rename", json={"name": "streamflow-v2"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["project_id"] == "streamflow-v2"
    assert (root / "streamflow-v2").is_dir()

    # The old id is gone: a retry is a clean 404, not a second move.
    assert client.post(
        "/api/workflows/projects/streamflow/rename", json={"name": "x"}
    ).status_code == 404
