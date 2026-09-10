"""Regression coverage for editable mirrors, generation barriers and deletion.

Transport uses a second temporary directory as the submit host. No SSH, Condor
or Pegasus command is executed and no developer workspace is touched.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from services import backends
from services.backends.base import ExecResult
from services.backends.local import LocalBackend
from services.backends.mirror import MirrorState
from services.backends.ssh import SshBackend, SshBackendConfig


def put(root, name, text):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def transport(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    backend = SshBackend(SshBackendConfig(
        id="remote-a", host="unused.invalid", remote_home=str(tmp_path / "host"),
        mirror_root=str(tmp_path / "mirror"),
    ))
    host = Path(backend.remote_home()) / "work"
    host.mkdir(parents=True)
    mirror = Path(backend.workspace_root())
    calls = []

    async def rsync(src, dst, extra=None):
        extra = extra or []
        if src.startswith(backend.id + ":"):
            src = src.split(":", 1)[1]
            if "--delete" in extra:
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True)
        else:
            dst = dst.split(":", 1)[1]
            names = Path(extra[extra.index("--files-from") + 1]).read_bytes().split(b"\0")
            for name in filter(None, names):
                relative = os.fsdecode(name)
                target = Path(dst) / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(src) / relative, target, follow_symlinks=False)
                calls.append(("upload", relative))
        return ""

    async def execute(argv, *, cwd=None, timeout, env=None):
        calls.append(tuple(argv))
        if argv[0] == "mkdir":
            Path(argv[-1]).mkdir(parents=True, exist_ok=True)
        elif argv[:2] == ["python3", "-c"]:
            # Exercise the exact deletion validation against only temp paths.
            result = subprocess.run([sys.executable, *argv[1:]], capture_output=True)
            return ExecResult(result.returncode, result.stdout, result.stderr)
        return ExecResult(0, b"", b"")

    monkeypatch.setattr(backend, "_run_rsync", rsync)
    monkeypatch.setattr(backend, "_exec", execute)
    monkeypatch.setattr(backends, "_REGISTRY", {"local": LocalBackend(), backend.id: backend})
    backends.set_active_backend_id(backend.id)
    return backend, host, mirror, calls


def test_background_pull_preserves_edits_and_reports_two_sided_conflicts(transport):
    b, host, mirror, _ = transport
    remote = put(host, "workflows/p/generator.py", "original")
    asyncio.run(b.pull_workspace())
    local = put(mirror, "workflows/p/generator.py", "saved edit")
    asyncio.run(b.pull_workspace())
    assert local.read_text() == "saved edit"
    remote.write_text("remote edit")
    asyncio.run(b.pull_workspace())
    assert local.read_text() == "saved edit"
    assert "conflict" in b.status()["error"].lower()
    with pytest.raises(RuntimeError, match="conflicts"):
        asyncio.run(b.push_project(str(local.parent)))
    assert remote.read_text() == "remote edit"
    # Making the copies agree resolves the conflict without changing metadata.
    local.write_text("remote edit")
    asyncio.run(b.push_project(str(local.parent)))
    assert b._mirror_state().conflicts == []


def test_push_sends_edits_but_never_stale_run_files(transport):
    b, host, mirror, calls = transport
    put(host, "workflows/p/generator.py", "old")
    log = put(host, "workflows/p/run/log", "running")
    asyncio.run(b.pull_workspace())
    put(mirror, "workflows/p/generator.py", "edited")
    log.write_text("finished")
    asyncio.run(b.push_project(str(mirror / "workflows/p")))
    assert (host / "workflows/p/generator.py").read_text() == "edited"
    assert (mirror / "workflows/p/run/log").read_text() == "finished"
    assert [c[1] for c in calls if c[0] == "upload"] == ["workflows/p/generator.py"]


def test_local_deletion_survives_restart_and_is_pushed(transport):
    b, host, mirror, _ = transport
    remote = put(host, "workflows/p/obsolete.py", "old")
    asyncio.run(b.pull_workspace())
    (mirror / "workflows/p/obsolete.py").unlink()
    b._mirror = None
    asyncio.run(b.pull_workspace())
    assert not (mirror / "workflows/p/obsolete.py").exists()
    asyncio.run(b.push_project(str(mirror / "workflows/p")))
    assert not remote.exists()


def test_failed_upload_does_not_acknowledge_untransferred_edits(transport, monkeypatch):
    b, host, mirror, _ = transport
    put(host, "workflows/p/a.py", "old")
    asyncio.run(b.pull_workspace())
    put(mirror, "workflows/p/a.py", "new")
    rsync = b._run_rsync

    async def fail_upload(src, dst, extra=None):
        return await rsync(src, dst, extra) if src.startswith(b.id + ":") else "connection lost"

    monkeypatch.setattr(b, "_run_rsync", fail_upload)
    with pytest.raises(RuntimeError, match="connection lost"):
        asyncio.run(b.push_project(str(mirror / "workflows/p")))
    asyncio.run(b.pull_workspace())
    assert (mirror / "workflows/p/a.py").read_text() == "new"
    assert b._mirror_state().changes(str(mirror / "workflows/p"))[0] == ["workflows/p/a.py"]


@pytest.mark.parametrize("existing", [False, True])
def test_generate_refreshes_yaml_before_immediate_plan(transport, monkeypatch, existing):
    from services import run_flow as rf

    b, host, mirror, _ = transport
    put(host, "workflows/p/generator.py", "generator")
    if existing:
        put(host, "workflows/p/workflow.yml", "stale yaml")
    asyncio.run(b.pull_workspace())
    execute = b._exec

    async def generate(argv, **kwargs):
        if argv[0] == "python3" and argv[1] == "generator.py":
            put(host, "workflows/p/workflow.yml", "fresh yaml")
        if argv[0] == "pegasus-plan":
            assert (host / "workflows/p/workflow.yml").read_text() == "fresh yaml"
        return await execute(argv, **kwargs)

    monkeypatch.setattr(b, "_exec", generate)
    flow = rf.RunFlow(flow_id="f", project_id="p", stages=[
        rf.Stage(key=k, label=k) for k in ("generate", "plan")
    ])

    async def run():
        assert await rf._run_generate(flow, project_dir=str(mirror / "workflows/p"), generator_file="generator.py", args=[])
        assert (mirror / "workflows/p/workflow.yml").read_text() == "fresh yaml"
        assert await rf._run_plan(flow, project_dir=str(mirror / "workflows/p"), site="condorpool", output_site="local")

    asyncio.run(run())


def test_delete_remote_project_cannot_resurrect_on_pull(transport):
    from services.workflow_submitter import delete_project

    b, host, mirror, _ = transport
    put(host, "workflows/p/generator.py", "source")
    put(host, "workflows/p/run/braindump.yml", "wf_uuid: uuid")
    asyncio.run(b.pull_workspace())
    assert asyncio.run(delete_project(str(mirror / "workflows/p")))["status"] == "deleted"
    asyncio.run(b.pull_workspace())
    assert not (host / "workflows/p").exists()
    assert not (mirror / "workflows/p").exists()


def test_delete_uses_remote_markers_when_local_run_is_missing(transport, monkeypatch):
    from services.workflow_submitter import delete_run

    b, host, mirror, calls = transport
    put(host, "workflows/p/run/braindump.yml", "wf_uuid: uuid")
    put(host, "workflows/p/run/monitord.started", "1")
    asyncio.run(b.pull_workspace())
    shutil.rmtree(mirror / "workflows/p/run")
    execute = b._exec

    async def fail_cancel(argv, **kwargs):
        if argv[0] == "pegasus-remove":
            return ExecResult(1, b"", b"unable to cancel")
        return await execute(argv, **kwargs)

    monkeypatch.setattr(b, "_exec", fail_cancel)
    result = asyncio.run(delete_run(str(mirror / "workflows/p/run")))
    assert result["status"] == "error"
    assert (host / "workflows/p/run/braindump.yml").exists()


def test_completed_remote_run_can_be_deleted(transport):
    from services.workflow_submitter import delete_run

    b, host, mirror, calls = transport
    put(host, "workflows/p/run/braindump.yml", "wf_uuid: uuid")
    put(host, "workflows/p/run/monitord.started", "1")
    put(host, "workflows/p/run/w.dag.dagman.out", "09/10/26 12:00:00 (1) EXITING WITH STATUS 0\n")
    asyncio.run(b.pull_workspace())
    assert asyncio.run(delete_run(str(mirror / "workflows/p/run")))["status"] == "deleted"
    assert not any(c[0] == "pegasus-remove" for c in calls)


def test_remote_delete_rejects_parent_symlink_and_unlinks_final_symlink(transport, tmp_path):
    b, host, mirror, _ = transport
    outside = tmp_path / "outside"
    outside.mkdir()
    keep = put(outside, "keep", "important")
    (host / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="outside"):
        asyncio.run(b._delete_remote([str(mirror / "escape/keep")], tree=False))
    asyncio.run(b._delete_remote([str(mirror / "escape")], tree=True))
    assert keep.read_text() == "important"
    assert not (host / "escape").is_symlink()


def test_mirror_rejects_symlink_parent_in_editable_copy(tmp_path):
    ws, state_dir, outside = tmp_path / "work", tmp_path / "state", tmp_path / "outside"
    ws.mkdir()
    outside.mkdir()
    (ws / "p").symlink_to(outside, target_is_directory=True)
    state = MirrorState(str(ws), str(state_dir))
    put(state.snapshot, "p/new", "remote")
    assert "p/new" in state.reconcile()
    assert not (outside / "new").exists()


def test_remote_deletion_preserves_an_editor_save_during_reconciliation(tmp_path, monkeypatch):
    state = MirrorState(str(tmp_path / "work"), str(tmp_path / "state"))
    remote = put(state.snapshot, "p/a.py", "shared")
    state.reconcile()
    remote.unlink()
    original_inventory = state.inventory

    def inventory(root):
        result = original_inventory(root)
        if root == state.workspace:
            put(state.workspace, "p/a.py", "saved during sync")
        return result

    monkeypatch.setattr(state, "inventory", inventory)
    assert state.reconcile() == ["p/a.py"]
    assert (state.workspace / "p/a.py").read_text() == "saved during sync"


def test_generate_refresh_failure_is_reported_instead_of_success(transport, monkeypatch):
    from services import run_flow as rf

    b, host, mirror, _ = transport
    put(host, "workflows/p/generator.py", "generator")
    asyncio.run(b.pull_workspace())
    execute = b._exec

    async def generate(argv, **kwargs):
        if argv[:2] == ["python3", "generator.py"]:
            async def disconnected(*args, **kwargs):
                return "connection lost after generation"
            monkeypatch.setattr(b, "_run_rsync", disconnected)
        return await execute(argv, **kwargs)

    monkeypatch.setattr(b, "_exec", generate)
    flow = rf.RunFlow(flow_id="f", project_id="p", stages=[rf.Stage(key="generate", label="Generate")])
    with pytest.raises(RuntimeError, match="connection lost after generation"):
        asyncio.run(rf._run_generate(flow, project_dir=str(mirror / "workflows/p"), generator_file="generator.py", args=[]))


def test_project_at_workspace_root_remains_supported(transport):
    b, host, mirror, _ = transport
    put(mirror, "workflow.yml", "root workflow")
    asyncio.run(b.push_project(str(mirror)))
    assert (host / "workflow.yml").read_text() == "root workflow"


def test_plan_does_not_report_stale_mirrored_files_as_success(transport, monkeypatch):
    b, host, mirror, _ = transport
    put(host, "workflows/p/workflow.yml", "workflow")
    asyncio.run(b.pull_workspace())
    execute = b._exec

    async def plan(argv, **kwargs):
        if argv[0] == "pegasus-plan":
            put(host, "workflows/p/run/braindump.yml", "remote plan")
            put(mirror, "workflows/p/run/braindump.yml", "concurrent local change")
        return await execute(argv, **kwargs)

    monkeypatch.setattr(b, "_exec", plan)
    with pytest.raises(RuntimeError, match="conflicts"):
        asyncio.run(b.exec(["pegasus-plan", "workflow.yml"], cwd=str(mirror / "workflows/p"), timeout=10))
    assert (mirror / "workflows/p/run/braindump.yml").read_text() == "concurrent local change"
