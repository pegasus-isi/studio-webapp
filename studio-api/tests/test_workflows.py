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
    """Create a mock Pegasus run directory and verify it's discovered."""
    home = os.environ.get("HOME", "/tmp")
    workspace = os.path.join(home, "work")
    run_dir = os.path.join(workspace, "test-run-001")
    os.makedirs(run_dir, exist_ok=True)

    # Write a minimal braindump.yml
    with open(os.path.join(run_dir, "braindump.yml"), "w") as f:
        f.write("pegasus_wf_name: test-workflow\n")
        f.write("wf_uuid: test-uuid-001\n")

    # Patch the scanner's WORKSPACE_ROOT
    import services.workflow_scanner as scanner

    old_root = scanner.WORKSPACE_ROOT
    scanner.WORKSPACE_ROOT = workspace

    # Also patch the router's WORKSPACE_ROOT
    import routers.workflows as wf_router

    old_wf_root = wf_router.WORKSPACE_ROOT
    wf_router.WORKSPACE_ROOT = workspace

    try:
        resp = client.get("/api/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["workflows"]) >= 1
        wf = data["workflows"][0]
        assert wf["name"] == "test-workflow"
        assert wf["run_id"] == "test-uuid-001"
    finally:
        scanner.WORKSPACE_ROOT = old_root
        wf_router.WORKSPACE_ROOT = old_wf_root


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
