"""Ownership and lifecycle regressions when Settings changes compute targets."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from services import backends
from services.backends.base import ExecResult
from services.backends.local import LocalBackend
from services.backends.ssh import SshBackend, SshBackendConfig


def setup_backends(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    local = LocalBackend()
    a = SshBackend(SshBackendConfig(id="a", host="a.invalid", mirror_root=str(tmp_path / "a")))
    b = SshBackend(SshBackendConfig(id="b", host="b.invalid", mirror_root=str(tmp_path / "b")))
    monkeypatch.setattr(backends, "_REGISTRY", {"local": local, "a": a, "b": b})
    backends.set_active_backend_id("b")
    return local, a, b


def test_cancel_and_analyze_use_owner_after_switch(tmp_path, monkeypatch):
    from services.workflow_submitter import cancel_workflow
    from services.workflow_monitor import run_pegasus_analyzer

    local, a, b = setup_backends(tmp_path, monkeypatch)
    run = str(Path(a.workflows_dir()) / "p/run0001")
    calls = []

    async def execute(argv, **kwargs):
        calls.append(argv)
        return ExecResult(0, b"owner-a", b"")

    monkeypatch.setattr(a, "exec", execute)
    monkeypatch.setattr(b, "exec", AsyncMock(side_effect=AssertionError("wrong backend")))
    assert asyncio.run(cancel_workflow(run))["status"] == "cancelled"
    assert "owner-a" in asyncio.run(run_pegasus_analyzer(run))
    assert [c[0] for c in calls] == ["pegasus-remove", "pegasus-analyzer"]
    assert backends.backend_for_path(str(tmp_path / "work/local-run")) is local


def test_operation_and_background_children_keep_original_selection(tmp_path, monkeypatch):
    _, a, b = setup_backends(tmp_path, monkeypatch)

    async def run():
        ready = asyncio.Event()
        proceed = asyncio.Event()

        async def child():
            ready.set()
            await proceed.wait()
            return backends.active_backend()

        with backends.using_backend(a):
            task = asyncio.create_task(child())
        await ready.wait()
        backends.set_active_backend_id(b.id)
        proceed.set()
        assert await task is a
        assert backends.active_backend() is b

    asyncio.run(run())


def test_flow_state_is_separate_for_same_project_on_two_backends(tmp_path, monkeypatch):
    from services import run_flow as rf

    _, a, b = setup_backends(tmp_path, monkeypatch)
    manager = rf.RunFlowManager()
    observed = []

    async def execute(flow, **kwargs):
        await asyncio.sleep(0)
        observed.append(backends.active_backend().id)

    monkeypatch.setattr(rf, "_execute", execute)

    async def run():
        kwargs = dict(project_id="same-name", project_dir="unused", generator_file=None,
                      has_container_def=False, generator_args=[], site="local", output_site="local")
        backends.set_active_backend_id(a.id)
        first = manager.start(**kwargs)
        backends.set_active_backend_id(b.id)
        assert manager.get("same-name") is None
        second = manager.start(**kwargs)
        assert manager.get("same-name") is second
        await asyncio.gather(first._task, second._task)
        assert observed == ["a", "b"]
        backends.set_active_backend_id(a.id)
        assert manager.get("same-name") is first

    asyncio.run(run())


def test_outputs_translate_with_owner_and_reject_unmirrored_storage(tmp_path, monkeypatch):
    import yaml
    from services import run_outputs

    _, a, _ = setup_backends(tmp_path, monkeypatch)
    run = Path(a.workflows_dir()) / "p/run0001"
    run.mkdir(parents=True)
    output = run.parent / "output"
    output.mkdir()
    (output / "result.txt").write_text("42")
    catalog = run / "sites.yml"

    def write(storage):
        catalog.write_text(yaml.safe_dump({"sites": [{"name": "local", "directories": [
            {"type": "localStorage", "path": storage}
        ]}]}))

    write(a.to_remote(str(output)))
    listing = run_outputs.listing(str(run), a.workflows_dir())
    assert listing["total"] == 1
    assert listing["output_dir"] == str(output)
    write(str(tmp_path / "private-unmirrored"))
    assert run_outputs.listing(str(run), a.workflows_dir())["output_dir"] is None


def test_local_scan_does_not_duplicate_configured_mirrors_or_sync_snapshots(tmp_path, monkeypatch):
    from services.workflow_scanner import discover_runs

    _, a, _ = setup_backends(tmp_path, monkeypatch)
    for root, uuid in [(tmp_path / "work", "local"),
                       (Path(a.workspace_root()), "remote"),
                       (tmp_path / ".studio/backend-sync/a/remote", "staging")]:
        run = root / "p/run0001"
        run.mkdir(parents=True)
        (run / "braindump.yml").write_text(f"wf_uuid: {uuid}\n")
    backends.set_active_backend_id("local")
    assert [r["run_id"] for r in discover_runs(str(tmp_path))] == ["local"]
    backends.set_active_backend_id("a")
    assert [r["run_id"] for r in discover_runs(a.scan_root())] == ["remote"]


def test_watcher_rebinds_context_and_root_without_dropping_subscribers(tmp_path, monkeypatch):
    from services.notification_engine import NotificationEngine

    _, a, b = setup_backends(tmp_path, monkeypatch)
    engine = NotificationEngine()
    roots = []

    async def watch():
        roots.append((engine._workspace_root, backends.active_backend().id))
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, "_watch_loop", watch)

    async def run():
        subscriber = engine.subscribe()
        with backends.using_backend(a):
            engine.start(None, a.scan_root())
        await asyncio.sleep(0)
        engine._seen["old"] = object()
        # An old caller context must not stick to the restarted watcher.
        with backends.using_backend(a):
            await engine.watch_backend(None, b)
        await asyncio.sleep(0)
        assert roots == [(a.scan_root(), "a"), (b.scan_root(), "b")]
        assert engine._seen == {}
        assert subscriber in engine._subscribers
        await engine.stop()

    asyncio.run(run())


def test_startup_restores_backend_before_starting_notifications(tmp_path, monkeypatch, request):
    from db import Database
    from services.notification_engine import engine

    _, a, _ = setup_backends(tmp_path, monkeypatch)
    backends.set_active_backend_id(None)
    original_get = Database.get_setting
    original_start = engine.start
    seen = []

    async def get_setting(self, key, default=None):
        if key == "execution_backend":
            return a.id
        return await original_get(self, key, default)

    def start(db, root):
        seen.append((root, backends.active_backend().id))
        original_start(db, root)

    monkeypatch.setattr(Database, "get_setting", get_setting)
    monkeypatch.setattr(a, "start", AsyncMock())
    monkeypatch.setattr(engine, "start", start)
    client = request.getfixturevalue("client")
    assert client.get("/api/settings/execution-backend").json()["backend"] == "a"
    assert seen == [(a.scan_root(), "a")]


def test_condor_cache_never_returns_another_backend_snapshot(tmp_path, monkeypatch):
    from services import condor_pool

    _, a, b = setup_backends(tmp_path, monkeypatch)
    seen = []

    async def query(argv, **kwargs):
        seen.append(backends.active_backend().id)
        return [], ""

    monkeypatch.setattr(condor_pool, "_cache", {"ts": 0, "data": None})
    monkeypatch.setattr(condor_pool, "_cache_lock", asyncio.Lock())
    monkeypatch.setattr(condor_pool, "_run_json", query)
    monkeypatch.setattr(condor_pool, "get_ospool_status", AsyncMock(return_value={}))

    async def run():
        with backends.using_backend(a):
            first = await condor_pool.get_status()
        with backends.using_backend(b):
            second = await condor_pool.get_status()
            assert await condor_pool.get_status() is second
        assert first is not second
        assert seen == ["a", "a", "b", "b"]

    asyncio.run(run())


def test_resolve_run_uses_its_mirror_not_the_selected_backend(tmp_path, monkeypatch):
    import routers.workflows as workflows

    _, a, b = setup_backends(tmp_path, monkeypatch)
    run = Path(a.workflows_dir()) / "p/run0001"
    run.mkdir(parents=True)
    monkeypatch.setattr(workflows, "_lookup_run_dir", AsyncMock(return_value=str(run)))
    ref = asyncio.run(workflows._resolve_run("uuid"))
    assert ref.backend is a
    assert backends.active_backend() is b


def test_outputs_do_not_follow_a_remote_symlink_into_local_files(tmp_path, monkeypatch):
    from services.run_outputs import resolve_output_dir

    _, a, _ = setup_backends(tmp_path, monkeypatch)
    run = Path(a.workflows_dir()) / "p/run0001"
    run.mkdir(parents=True)
    outside = tmp_path / "private"
    outside.mkdir()
    (run.parent / "output").symlink_to(outside, target_is_directory=True)
    assert resolve_output_dir(str(run), a.workflows_dir()) == (None, "none")
