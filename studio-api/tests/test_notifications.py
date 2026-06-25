"""Tests for the notification endpoints and engine pub/sub."""

from __future__ import annotations


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
