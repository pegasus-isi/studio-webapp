"""Tests for the dashboard aggregate endpoint and resource notifications."""

from __future__ import annotations


def test_dashboard_shape_empty_workspace(client, monkeypatch):
    """Fresh workspace, no condor: everything zero but the shape is complete."""
    from services import condor_pool

    async def fake_status(force=False):
        return {
            "available": False,
            "pool": {"available": False, "error": "condor_status not found"},
            "demand": {"available": False, "error": "condor_q not found"},
            "pressure": [],
        }

    monkeypatch.setattr(condor_pool, "get_status", fake_status)

    res = client.get("/api/dashboard")
    assert res.status_code == 200
    body = res.json()
    assert body["projects"] == {"total": 0, "by_status": {}}
    assert body["runs"]["total"] == 0
    assert body["runs"]["recent"] == []
    assert body["resources"]["available"] is False
    assert body["notifications"]["unread"] == 0
    assert "pegasus" in body["versions"]
    # AI showcase: empty knowledge root -> no skills, just the General persona.
    assert body["ai"]["skills"] == []
    assert [a["id"] for a in body["ai"]["agents"]] == ["general"]


def test_dashboard_lists_skills(client, monkeypatch, tmp_path):
    """Skills found under KNOWLEDGE_ROOT surface in the ai payload."""
    import json
    import os

    from routers import knowledge
    from services import condor_pool

    skills_dir = tmp_path / "skills" / "scaffold"
    skills_dir.mkdir(parents=True)
    (skills_dir / "metadata.json").write_text(json.dumps({
        "name": "Scaffold",
        "description": "Generate a complete Pegasus workflow project",
        "slash_command": "/scaffold",
        "order": 1,
    }))
    os.makedirs(tmp_path / "agents", exist_ok=True)
    monkeypatch.setattr(knowledge, "KNOWLEDGE_ROOT", str(tmp_path))

    async def fake_status(force=False):
        return {"available": False, "pool": {"available": False},
                "demand": {"available": False}, "pressure": []}

    monkeypatch.setattr(condor_pool, "get_status", fake_status)

    body = client.get("/api/dashboard").json()
    assert body["ai"]["skills"] == [{
        "name": "Scaffold",
        "description": "Generate a complete Pegasus workflow project",
        "slash_command": "/scaffold",
    }]


def test_dashboard_aggregates_runs(client, monkeypatch):
    from routers import dashboard as dash
    from services import condor_pool

    runs = [
        {"run_id": "u1", "run_name": "run0001", "name": "csv", "run_dir": "/r1",
         "timestamp": "20260701T000000Z"},
        {"run_id": "u2", "run_name": "run0002", "name": "csv", "run_dir": "/r2",
         "timestamp": "20260710T000000Z"},
    ]
    details = {
        "/r1": {"status": "succeeded", "total_jobs": 4, "completed_jobs": 4,
                "failed_jobs": 0},
        "/r2": {"status": "running", "total_jobs": 4, "completed_jobs": 1,
                "failed_jobs": 0},
    }

    async def fake_detail(run_dir):
        return details[run_dir]

    async def fake_status(force=False):
        return {"available": True, "pool": {"available": True},
                "demand": {"available": True}, "pressure": []}

    monkeypatch.setattr(dash, "discover_runs", lambda: runs)
    monkeypatch.setattr(dash, "discover_projects", lambda: [
        {"status": "generated"}, {"status": "draft"},
    ])
    monkeypatch.setattr(dash, "get_workflow_detail", fake_detail)
    monkeypatch.setattr(condor_pool, "get_status", fake_status)

    body = client.get("/api/dashboard").json()
    assert body["projects"] == {
        "total": 2, "by_status": {"generated": 1, "draft": 1}
    }
    assert body["runs"]["by_status"] == {"succeeded": 1, "running": 1}
    # Newest first.
    assert body["runs"]["recent"][0]["run_id"] == "u2"
    assert body["resources"]["available"] is True


def test_check_resources_notifies_on_pressure(tmp_path, monkeypatch):
    """A running workflow with starved jobs raises one 'resource' alert."""
    import asyncio

    from db import Database
    from services import condor_pool
    import services.notification_engine as ne

    async def fake_status(force=False):
        return {
            "available": True,
            "pool": {"available": True, "total_cpus": 4, "idle_cpus": 0,
                     "total_gpus": 0, "idle_gpus": 0},
            "demand": {
                "available": True,
                "by_workflow": {
                    "uuid-1": {
                        "name": "csv", "idle_jobs": 3, "running_jobs": 1,
                        "held_jobs": 0, "idle_cpus_requested": 8,
                        "idle_gpus_requested": 0,
                        "idle_memory_mb_requested": 0,
                        "oldest_idle_seconds": 60, "held_reason": None,
                    }
                },
            },
            "pressure": [],
        }

    monkeypatch.setattr(condor_pool, "get_status", fake_status)

    async def go():
        db = Database(str(tmp_path / "n.db"))
        await db.connect()
        eng = ne.NotificationEngine()
        eng._db = db

        detail = {"status": "running"}
        await eng._check_resources("uuid-1", "csv / run0001", detail)
        # Same pressure kind again: dedup_key refreshes, no second row.
        await eng._check_resources("uuid-1", "csv / run0001", detail)

        notes = await db.list_notifications()
        await db.close()
        return notes

    notes = asyncio.run(go())
    assert len(notes) == 1
    n = notes[0]
    assert n["kind"] == "resource"
    assert n["severity"] == "warning"
    assert "8 CPU(s)" in n["body"]
    assert n["link"] == "/provisioning"
    assert n["meta"]["pressure"] == "cpu_shortage"
    assert n["meta"]["delta"] == {"cpus": 8}


def test_check_resources_quiet_paths(tmp_path, monkeypatch):
    """No alert for non-running runs, missing condor, or no pressure."""
    import asyncio

    from services import condor_pool
    import services.notification_engine as ne

    calls = {"notify": 0}

    async def fake_notify(**kw):
        calls["notify"] += 1

    async def go(status, detail):
        async def fake_status(force=False):
            return status

        monkeypatch.setattr(condor_pool, "get_status", fake_status)
        eng = ne.NotificationEngine()
        eng.notify = fake_notify  # type: ignore[method-assign]
        await eng._check_resources("uuid-1", "csv", detail)

    ok_status = {
        "available": True,
        "pool": {"available": True, "idle_cpus": 16, "idle_gpus": 0,
                 "total_cpus": 16, "total_gpus": 0},
        "demand": {"available": True, "by_workflow": {}},
        "pressure": [],
    }
    asyncio.run(go(ok_status, {"status": "planned"}))       # not running
    asyncio.run(go({"available": False}, {"status": "running"}))  # no condor
    asyncio.run(go(ok_status, {"status": "running"}))       # no demand entry
    assert calls["notify"] == 0
