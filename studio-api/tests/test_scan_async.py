"""The async scanner wrappers off-load the blocking sync bodies (BE-1)."""

from __future__ import annotations

import asyncio

import services.workflow_scanner as ws


def test_async_wrappers_delegate_to_sync_bodies(monkeypatch):
    monkeypatch.setattr(
        ws, "discover_runs", lambda w=None, **kw: [{"run_id": "r1"}]
    )
    monkeypatch.setattr(
        ws, "discover_projects", lambda w=None, **kw: [{"project_id": "p1"}]
    )
    monkeypatch.setattr(ws, "get_run_status", lambda rd: "running")

    assert asyncio.run(ws.discover_runs_async()) == [{"run_id": "r1"}]
    assert asyncio.run(ws.discover_projects_async()) == [{"project_id": "p1"}]
    assert asyncio.run(ws.get_run_status_async("/x")) == "running"


def test_wrappers_pass_workspace_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        ws,
        "discover_runs",
        lambda w=None, **kw: seen.setdefault("runs", w) or [],
    )
    monkeypatch.setattr(
        ws,
        "discover_projects",
        lambda w=None, **kw: seen.setdefault("projects", w) or [],
    )

    asyncio.run(ws.discover_runs_async("/ws"))
    asyncio.run(ws.discover_projects_async("/ws"))
    assert seen == {"runs": "/ws", "projects": "/ws"}
