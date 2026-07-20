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


def test_find_generator_resolves_name_variants(tmp_path):
    """The generator resolver accepts the underscore and hyphen filenames and
    honors a name declared in .pegasushub.yml — mirroring the inconsistent
    naming across pegasus-isi repos."""
    from services.workflow_scanner import find_generator

    # No generator at all.
    assert find_generator(str(tmp_path)) is None

    # Underscore form (most repos).
    under = tmp_path / "under"
    under.mkdir()
    (under / "workflow_generator.py").write_text("# gen\n")
    assert find_generator(str(under)).endswith("workflow_generator.py")

    # Hyphen form (quickstart-workflow).
    hyphen = tmp_path / "hyphen"
    hyphen.mkdir()
    (hyphen / "workflow-generator.py").write_text("# gen\n")
    assert find_generator(str(hyphen)).endswith("workflow-generator.py")

    # Custom name declared in the PegasusHub marker (variant-calling).
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / ".pegasushub.yml").write_text(
        "scripts:\n  generator: variant-calling.py\n"
    )
    (custom / "variant-calling.py").write_text("# gen\n")
    assert find_generator(str(custom)).endswith("variant-calling.py")

    # A marker pointing at a missing file falls through to None.
    dangling = tmp_path / "dangling"
    dangling.mkdir()
    (dangling / ".pegasushub.yml").write_text("scripts:\n  generator: nope.py\n")
    assert find_generator(str(dangling)) is None


def test_discover_projects_lists_marketplace_clones(tmp_path):
    """A freshly-cloned quickstart (hyphenated generator, no YAML) and a
    registry-only repo (.pegasushub.yml) both appear on the Workflows page.

    Regression: previously discover_projects only recognized
    ``workflow_generator.py`` (underscore) + a top-level YAML, so quickstart
    was cloned but never listed."""
    from services.workflow_scanner import discover_projects

    quickstart = tmp_path / "quickstart"
    quickstart.mkdir()
    (quickstart / "workflow-generator.py").write_text("# gen\n")
    (quickstart / "README.md").write_text("# quickstart\n")

    variant = tmp_path / "variant-calling"
    variant.mkdir()
    (variant / ".pegasushub.yml").write_text(
        "scripts:\n  generator: variant-calling.py\n"
    )
    (variant / "variant-calling.py").write_text("# gen\n")

    # A plain directory that isn't a workflow project stays hidden.
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "todo.txt").write_text("x\n")

    projects = discover_projects(workspace=str(tmp_path))
    by_id = {p["project_id"]: p for p in projects}

    assert "quickstart" in by_id, "hyphenated-generator clone must be listed"
    assert by_id["quickstart"]["has_generator"] is True
    assert by_id["quickstart"]["generator_file"] == "workflow-generator.py"

    assert "variant-calling" in by_id, "pegasushub-only repo must be listed"
    assert by_id["variant-calling"]["generator_file"] == "variant-calling.py"

    assert "notes" not in by_id


def test_generate_uses_resolved_generator_filename(client, monkeypatch, tmp_path):
    """The Generate action invokes the project's actual generator filename,
    not a hardcoded workflow_generator.py."""
    import routers.workflows as wf

    proj_dir = tmp_path / "quickstart"
    proj_dir.mkdir()
    (proj_dir / "workflow-generator.py").write_text("# gen\n")

    monkeypatch.setattr(
        wf,
        "discover_projects",
        lambda: [{
            "project_id": "quickstart",
            "project_dir": str(proj_dir),
            "has_generator": True,
            "generator_file": "workflow-generator.py",
        }],
    )

    captured: dict = {}

    async def fake_exec(*cmd, **kw):
        captured["cmd"] = cmd

        class P:
            returncode = 0

            async def communicate(self):
                return (b"ok", b"")

        return P()

    monkeypatch.setattr(wf.asyncio, "create_subprocess_exec", fake_exec)

    resp = client.post("/api/workflows/projects/quickstart/generate", json={"args": []})
    assert resp.status_code == 200
    assert captured["cmd"][:2] == ("python3", "workflow-generator.py")


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


def test_delete_run_not_found(client):
    assert client.delete("/api/workflows/nope-run-id").status_code == 404


def test_delete_run_removes_dir_and_db_row(client, monkeypatch):
    """Deleting a run removes its submit directory from disk and drops it from
    the persisted run list."""
    import asyncio

    import services.workflow_scanner as scanner
    from main import db

    home = os.environ.get("HOME", "/tmp")
    run_dir = os.path.join(home, "work", "workflows", "quickstart", "run0001")
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "braindump.yml"), "w") as f:
        f.write("pegasus_wf_name: quickstart\nwf_uuid: del-uuid-1\n")

    # Confine the scan root to our temp home so realpath validation passes.
    monkeypatch.setattr(scanner, "SCAN_ROOT", os.path.join(home, "work"))
    asyncio.run(db.upsert_workflow_run(
        run_id="del-uuid-1", name="quickstart", run_dir=run_dir, status="succeeded"
    ))

    resp = client.delete("/api/workflows/del-uuid-1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    assert not os.path.exists(run_dir)
    assert all(r["run_id"] != "del-uuid-1" for r in asyncio.run(db.list_workflow_runs()))


def test_delete_run_rejects_path_outside_scan_root(client, monkeypatch, tmp_path):
    """A run dir resolving outside the scan root is refused (never rm'd)."""
    import asyncio

    import services.workflow_scanner as scanner
    from main import db

    outside = tmp_path / "outside-run"
    outside.mkdir()
    (outside / "keep.txt").write_text("do not delete\n")

    # Scan root is elsewhere, so `outside` is out of bounds.
    monkeypatch.setattr(scanner, "SCAN_ROOT", str(tmp_path / "sandbox"))
    asyncio.run(db.upsert_workflow_run(
        run_id="evil-1", name="x", run_dir=str(outside), status="succeeded"
    ))

    resp = client.delete("/api/workflows/evil-1")
    assert resp.status_code == 403
    assert outside.exists()  # untouched


def test_delete_run_retries_then_succeeds(tmp_path, monkeypatch):
    """A transient 'device or resource busy' (NFS handle not yet released) is
    retried, and the delete succeeds once rmtree stops failing."""
    import asyncio

    import services.workflow_submitter as ws

    run_dir = tmp_path / "run0001"
    run_dir.mkdir()
    (run_dir / "braindump.yml").write_text("x: 1\n")

    calls = {"n": 0}
    real_rmtree = ws.shutil.rmtree

    def flaky_rmtree(path, *a, **k):
        calls["n"] += 1
        if calls["n"] < 3:  # busy on the first two attempts
            raise OSError(16, "Device or resource busy")
        real_rmtree(path, *a, **k)

    monkeypatch.setattr(ws.shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(ws, "_DELETE_RETRY_DELAY", 0)  # no real waiting

    result = asyncio.run(ws.delete_run(str(run_dir)))
    assert result["status"] == "deleted"
    assert calls["n"] == 3
    assert not run_dir.exists()


def test_delete_run_partial_when_files_held(tmp_path, monkeypatch):
    """If files stay busy, delete_run removes what it can — dropping
    braindump.yml so the run leaves the list — and still reports success."""
    import asyncio

    import services.workflow_submitter as ws

    run_dir = tmp_path / "run0002"
    run_dir.mkdir()
    (run_dir / "braindump.yml").write_text("x: 1\n")
    (run_dir / ".nfs00deadbeef").write_text("held\n")

    # Every full rmtree "fails busy"; the best-effort pass removes braindump.yml
    # but leaves the .nfs stub (as NFS would).
    def always_busy(path, *a, **k):
        raise OSError(16, "Device or resource busy")

    def fake_best_effort(rd):
        os.remove(os.path.join(rd, "braindump.yml"))
        return [os.path.join(rd, ".nfs00deadbeef")]

    monkeypatch.setattr(ws.shutil, "rmtree", always_busy)
    monkeypatch.setattr(ws, "_best_effort_rmtree", fake_best_effort)
    monkeypatch.setattr(ws, "_DELETE_RETRY_DELAY", 0)

    result = asyncio.run(ws.delete_run(str(run_dir)))
    assert result["status"] == "deleted"  # run no longer discoverable
    assert not (run_dir / "braindump.yml").exists()


def test_is_submitted_detects_markers(tmp_path):
    """A run dir counts as submitted once monitord/DAGMan have started."""
    from services.workflow_submitter import _is_submitted

    planned = tmp_path / "run0001"
    planned.mkdir()
    (planned / "braindump.yml").write_text("x: 1\n")
    assert _is_submitted(str(planned)) is False  # plan-only

    for marker in ("monitord.pid", "monitord.started", "wf-0.dag.dagman.out"):
        d = tmp_path / marker.replace(".", "_")
        d.mkdir()
        (d / "braindump.yml").write_text("x: 1\n")
        (d / marker).write_text("")
        assert _is_submitted(str(d)) is True, marker


def test_run_reuses_planned_dir_only_if_unsubmitted(tmp_path, monkeypatch):
    """run_planned_workflow pegasus-runs a provided unsubmitted dir, but
    plan+submits fresh when that dir is already submitted."""
    import asyncio
    import services.workflow_submitter as ws

    planned = tmp_path / "run0006"
    planned.mkdir()
    (planned / "braindump.yml").write_text("x: 1\n")

    ran: dict = {}

    async def fake_pegasus_run(*cmd, **kw):
        ran["target"] = cmd[-1]

        class P:
            returncode = 0

            async def communicate(self):
                return (b"", b"")

        return P()

    async def fake_submit(workflow_dir, **kw):
        ran["fresh_plan_submit"] = True
        return {"status": "submitted", "run_dir": "fresh", "output": ""}

    monkeypatch.setattr(ws.asyncio, "create_subprocess_exec", fake_pegasus_run)
    monkeypatch.setattr(ws, "submit_workflow", fake_submit)

    # Unsubmitted planned dir -> reused via pegasus-run.
    asyncio.run(ws.run_planned_workflow(str(tmp_path), run_dir=str(planned)))
    assert ran.get("target") == str(planned)
    assert "fresh_plan_submit" not in ran

    # Same dir, now submitted -> Run must NOT re-run it; plan+submit fresh.
    ran.clear()
    (planned / "monitord.started").write_text("")
    asyncio.run(ws.run_planned_workflow(str(tmp_path), run_dir=str(planned)))
    assert ran.get("fresh_plan_submit") is True
    assert "target" not in ran
