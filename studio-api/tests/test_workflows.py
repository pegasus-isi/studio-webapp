"""Tests for workflow endpoints."""

from __future__ import annotations

import os


def test_list_workflows_empty(client):
    resp = client.get("/api/workflows")
    assert resp.status_code == 200
    data = resp.json()
    assert "workflows" in data
    assert isinstance(data["workflows"], list)


def test_list_workflows_with_braindump(client):
    """A run dir under ~/work/workflows is discovered; a cloned-repo/test
    fixture braindump elsewhere in the tree is excluded."""
    home = os.environ.get("HOME", "/tmp")
    workflows_dir = os.path.join(home, "work", "workflows")

    # A genuine run dir — should be discovered.
    run_dir = os.path.join(workflows_dir, "test-run-001")
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "braindump.yml"), "w") as f:
        f.write("pegasus_wf_name: test-workflow\n")
        f.write("wf_uuid: test-uuid-001\n")

    # A braindump inside a cloned repo's test fixtures — must be ignored.
    fixture_dir = os.path.join(
        workflows_dir, "pegasus-clone", "test", "analyzer_samples_dir", "sample1"
    )
    os.makedirs(fixture_dir, exist_ok=True)
    with open(os.path.join(fixture_dir, "braindump.yml"), "w") as f:
        f.write("pegasus_wf_name: fixture-should-be-hidden\n")
        f.write("wf_uuid: fixture-uuid\n")

    # discover_runs() walks SCAN_ROOT; point it at our temp workflows tree.
    import services.workflow_scanner as scanner

    old_root = scanner.SCAN_ROOT
    scanner.SCAN_ROOT = workflows_dir

    try:
        resp = client.get("/api/workflows")
        assert resp.status_code == 200
        data = resp.json()
        names = [w["name"] for w in data["workflows"]]
        assert "test-workflow" in names
        assert "fixture-should-be-hidden" not in names
        wf = next(w for w in data["workflows"] if w["name"] == "test-workflow")
        assert wf["run_id"] == "test-uuid-001"
    finally:
        scanner.SCAN_ROOT = old_root


def test_get_workflow_not_found(client):
    resp = client.get("/api/workflows/nonexistent-id")
    assert resp.status_code == 404


def test_submit_workflow_forbidden(client):
    """Submitting outside workspace should be rejected."""
    resp = client.post("/api/workflows/submit?workflow_dir=/etc/passwd")
    assert resp.status_code == 403


def test_workflow_jobs_not_found(client):
    resp = client.get("/api/workflows/nonexistent/jobs")
    assert resp.status_code == 404
