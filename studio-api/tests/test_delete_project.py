"""Tests for deleting a whole workflow project.

This removes a directory tree, so the tests weight the guards more heavily than
the happy path: an unknown id must not name something to delete, a project
resolving outside the workflows directory must be refused, and a run that
cannot be removed must abort the whole operation rather than leaving a
half-deleted project with live jobs in the queue.
"""

from __future__ import annotations

import asyncio
import os

from services import workflow_submitter as ws


def _project(tmp_path, runs=()):
    project = tmp_path / "workflows" / "my-analysis"
    project.mkdir(parents=True)
    (project / "workflow_generator.py").write_text("pass\n")
    for name in runs:
        run = project / name
        run.mkdir()
        (run / "braindump.yml").write_text("wf_uuid: abc\n")
    return str(project)


def test_deletes_a_project_with_no_runs(tmp_path):
    project = _project(tmp_path)
    result = asyncio.run(ws.delete_project(project))
    assert result["status"] == "deleted"
    assert not os.path.exists(project)


def test_deletes_runs_before_the_project_directory(tmp_path, monkeypatch):
    """Runs go through delete_run so the Condor queue is left consistent.

    rmtree'ing the project outright would leave any queued jobs referencing a
    submit directory that no longer exists, with no way to reach them.
    """
    project = _project(tmp_path, runs=["run0001", "run0002"])
    deleted: list[str] = []

    async def fake_delete_run(run_dir):
        deleted.append(os.path.basename(run_dir))
        return {"status": "deleted"}

    monkeypatch.setattr(ws, "delete_run", fake_delete_run)

    result = asyncio.run(ws.delete_project(project))
    assert result["status"] == "deleted"
    assert sorted(deleted) == ["run0001", "run0002"]
    assert not os.path.exists(project)


def test_a_run_that_cannot_be_deleted_aborts_the_whole_operation(
    tmp_path, monkeypatch
):
    # A half-deleted project with live jobs is worse than the state we started
    # in, so the project directory must survive for the user to retry.
    project = _project(tmp_path, runs=["run0001"])

    async def stuck(run_dir):
        return {"status": "error", "output": "device or resource busy"}

    monkeypatch.setattr(ws, "delete_run", stuck)

    result = asyncio.run(ws.delete_project(project))
    assert result["status"] == "error"
    assert "busy" in result["output"]
    assert os.path.isdir(project), "project must survive a blocked run"


def test_deleting_an_already_gone_project_is_not_an_error(tmp_path):
    # Two clicks, or a stale list: the second should report success rather
    # than an error about a directory that is already in the desired state.
    result = asyncio.run(ws.delete_project(str(tmp_path / "not-there")))
    assert result["status"] == "deleted"


# ── endpoint guards ───────────────────────────────────────────────────────


def test_endpoint_404s_for_an_unknown_project(client):
    # The id must name a project the scanner found; it can never be a path.
    res = client.delete("/api/workflows/projects/no-such-project")
    assert res.status_code == 404


def test_endpoint_404s_for_a_traversal_attempt(client):
    res = client.delete("/api/workflows/projects/..%2F..%2Fetc")
    assert res.status_code in (404, 405)
