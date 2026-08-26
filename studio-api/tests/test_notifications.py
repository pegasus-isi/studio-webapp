"""Tests for the notification endpoints and engine pub/sub."""

from __future__ import annotations


def _aconst(value):
    """Return an async function that ignores its args and returns `value` —
    for monkeypatching the scanner's async wrappers (BE-1)."""

    async def _f(*args, **kwargs):
        return value

    return _f


def test_list_empty(client):
    res = client.get("/api/notifications")
    assert res.status_code == 200
    body = res.json()
    assert body["notifications"] == []
    assert body["unread"] == 0


def test_create_and_list(client):
    res = client.post(
        "/api/notifications",
        json={"title": "Hello", "body": "world", "severity": "info"},
    )
    assert res.status_code == 200
    created = res.json()
    assert created["title"] == "Hello"
    assert created["read"] is False

    res = client.get("/api/notifications")
    body = res.json()
    assert len(body["notifications"]) == 1
    assert body["unread"] == 1


def test_mark_read_and_read_all(client):
    a = client.post("/api/notifications", json={"title": "a"}).json()
    client.post("/api/notifications", json={"title": "b"})

    client.post(f"/api/notifications/{a['id']}/read")
    assert client.get("/api/notifications").json()["unread"] == 1

    client.post("/api/notifications/read-all")
    assert client.get("/api/notifications").json()["unread"] == 0


def test_dedup_key_collapses_unread(client):
    client.post(
        "/api/notifications",
        json={"title": "v1", "dedup_key": "state:run1:running"},
    )
    client.post(
        "/api/notifications",
        json={"title": "v2", "dedup_key": "state:run1:running"},
    )
    body = client.get("/api/notifications").json()
    assert len(body["notifications"]) == 1
    assert body["notifications"][0]["title"] == "v2"


def test_mute_records_as_read(client):
    # Default: nothing muted.
    assert client.get("/api/notifications/prefs").json()["muted"] == []

    # Mute the job_failure category.
    res = client.put(
        "/api/notifications/prefs", json={"muted": ["job_failure"]}
    )
    assert res.status_code == 200
    assert res.json()["muted"] == ["job_failure"]

    # A muted notification is still created, but marked read.
    muted = client.post(
        "/api/notifications", json={"title": "boom", "kind": "job_failure"}
    ).json()
    assert muted["read"] is True
    # A non-muted kind arrives unread.
    client.post(
        "/api/notifications", json={"title": "ok", "kind": "workflow_state"}
    )

    body = client.get("/api/notifications").json()
    titles = {n["title"] for n in body["notifications"]}
    # Both land in history...
    assert titles == {"boom", "ok"}
    # ...but only the non-muted one counts toward the unread badge.
    assert body["unread"] == 1

    # Un-mute and confirm new ones arrive unread again.
    client.put("/api/notifications/prefs", json={"muted": []})
    later = client.post(
        "/api/notifications", json={"title": "boom2", "kind": "job_failure"}
    ).json()
    assert later["read"] is False


def test_delete_and_clear(client):
    a = client.post("/api/notifications", json={"title": "a"}).json()
    client.post("/api/notifications", json={"title": "b"})

    client.delete(f"/api/notifications/{a['id']}")
    assert len(client.get("/api/notifications").json()["notifications"]) == 1

    client.delete("/api/notifications")
    assert client.get("/api/notifications").json()["notifications"] == []


# --- run-start (#1) and seed-terminal (#2) notifications ---


def test_notification_exists_helper(tmp_path):
    import asyncio

    from db import Database

    async def go():
        db = Database(str(tmp_path / "n.db"))
        await db.connect()
        assert await db.notification_exists("k1") is False
        await db.add_notification(kind="workflow_state", title="x", dedup_key="k1")
        assert await db.notification_exists("k1") is True
        await db.close()

    asyncio.run(go())


def test_dedup_refresh_preserves_read(tmp_path):
    """A dedup refresh (the watch loop re-touching a still-live alert) must not
    flip an already-read notification back to unread — otherwise marking it
    read never sticks."""
    import asyncio

    from db import Database

    async def go():
        db = Database(str(tmp_path / "n.db"))
        await db.connect()
        n = await db.add_notification(
            kind="resource", title="pressure", dedup_key="resource:run1:cpu"
        )
        assert n["read"] is False
        await db.mark_notification_read(n["id"])

        # Same dedup_key arrives again "unread" from the poll loop.
        refreshed = await db.add_notification(
            kind="resource",
            title="pressure (updated)",
            dedup_key="resource:run1:cpu",
        )
        assert refreshed["id"] == n["id"]  # same row, refreshed in place
        assert refreshed["title"] == "pressure (updated)"  # content still updates
        assert refreshed["read"] is True  # ...but stays read
        assert await db.unread_notification_count() == 0
        await db.close()

    asyncio.run(go())


def test_notification_exists_survives_clear(tmp_path):
    """The durable ledger keeps a delivered dedup_key known even after the user
    clears the visible feed, so fire-once guards stay satisfied."""
    import asyncio

    from db import Database

    async def go():
        db = Database(str(tmp_path / "n.db"))
        await db.connect()
        await db.add_notification(
            kind="workflow_state", title="done", dedup_key="state:run1:succeeded"
        )
        assert await db.notification_exists("state:run1:succeeded") is True

        await db.clear_notifications()
        assert await db.list_notifications() == []
        assert await db.notification_exists("state:run1:succeeded") is True
        await db.close()

    asyncio.run(go())


def test_seed_emits_terminal_notification_once(tmp_path, monkeypatch):
    """A run already terminal at first sighting gets one notification, and a
    second seed (engine restart) does not duplicate it."""
    import asyncio

    from db import Database
    import services.notification_engine as ne

    async def go():
        db = Database(str(tmp_path / "n.db"))
        await db.connect()

        runs = [{
            "run_dir": str(tmp_path / "run0001"),
            "run_id": "uuid-1",
            "run_name": "run0001",
            "name": "csv_summary",
        }]
        monkeypatch.setattr(ne, "discover_runs_async", _aconst(runs))

        async def fake_detail(run_dir):
            return {"status": "failed", "failed_jobs": 1, "total_jobs": 8}

        monkeypatch.setattr(ne, "get_workflow_detail", fake_detail)
        monkeypatch.setattr(ne, "get_run_status_async", _aconst("failed"))

        eng = ne.NotificationEngine()
        eng._db = db
        eng._workspace_root = str(tmp_path)

        await eng._scan(seed_only=True)
        notes = await db.list_notifications()
        assert len(notes) == 1
        assert "failed" in notes[0]["title"].lower()
        assert "csv_summary / run0001" in notes[0]["title"]

        # Simulate an engine restart: fresh instance, same db -> no duplicate.
        eng2 = ne.NotificationEngine()
        eng2._db = db
        eng2._workspace_root = str(tmp_path)
        await eng2._scan(seed_only=True)
        assert len(await db.list_notifications()) == 1

        await db.close()

    asyncio.run(go())


def test_seed_does_not_resurrect_cleared_terminal(tmp_path, monkeypatch):
    """After the user clears a terminal-state alert, an engine restart (re-seed)
    must not bring it back — the regression behind "cleared notifications
    reappear after logout/login"."""
    import asyncio

    from db import Database
    import services.notification_engine as ne

    async def go():
        db = Database(str(tmp_path / "n.db"))
        await db.connect()

        runs = [{
            "run_dir": str(tmp_path / "run0001"),
            "run_id": "uuid-1",
            "run_name": "run0001",
            "name": "csv_summary",
        }]
        monkeypatch.setattr(ne, "discover_runs_async", _aconst(runs))

        async def fake_detail(run_dir):
            return {"status": "failed", "failed_jobs": 1, "total_jobs": 8}

        monkeypatch.setattr(ne, "get_workflow_detail", fake_detail)
        monkeypatch.setattr(ne, "get_run_status_async", _aconst("failed"))

        eng = ne.NotificationEngine()
        eng._db = db
        eng._workspace_root = str(tmp_path)
        await eng._scan(seed_only=True)
        assert len(await db.list_notifications()) == 1

        # User clears the feed, then the server restarts and re-seeds.
        await db.clear_notifications()
        eng2 = ne.NotificationEngine()
        eng2._db = db
        eng2._workspace_root = str(tmp_path)
        await eng2._scan(seed_only=True)
        assert await db.list_notifications() == []

        await db.close()

    asyncio.run(go())


def test_notify_run_started(tmp_path, monkeypatch):
    """The submit helper raises a 'run started' alert labelled by workflow."""
    import asyncio

    from routers import workflows as wf
    import services.notification_engine as ne

    run_dir = tmp_path / "run0007"
    run_dir.mkdir()
    (run_dir / "braindump.yml").write_text(
        "wf_uuid: abc-123\npegasus_wf_name: csv_summary\n"
    )

    captured: dict = {}

    async def fake_notify(**kw):
        captured.update(kw)
        return None

    monkeypatch.setattr(ne.engine, "notify", fake_notify)

    asyncio.run(
        wf._notify_run_started({"status": "submitted", "run_dir": str(run_dir)})
    )
    assert captured["run_id"] == "abc-123"
    assert captured["title"] == "Workflow “csv_summary / run0007” run started"
    assert captured["dedup_key"] == "run-started:abc-123"


def test_notify_run_started_skips_non_submitted(monkeypatch):
    import asyncio

    from routers import workflows as wf
    import services.notification_engine as ne

    called = {"n": 0}

    async def fake_notify(**kw):
        called["n"] += 1

    monkeypatch.setattr(ne.engine, "notify", fake_notify)
    asyncio.run(wf._notify_run_started({"status": "error", "output": "boom"}))
    assert called["n"] == 0


def test_finished_run_notification_links_to_results(tmp_path):
    """A completed run should land the user on its results, not the job table.

    The files are what they were waiting for (#241, Phase 4). A failure is the
    opposite case: the diagnosis is in the steps, so it links there explicitly.
    """
    from services.notification_engine import _run_link

    assert _run_link("abc123", "succeeded") == "/workflows?run=abc123"
    assert _run_link("abc123", "failed") == "/workflows?run=abc123&view=steps"
    # Anything mid-flight also wants the steps — there are no results yet.
    assert _run_link("abc123", "running").endswith("&view=steps")
