"""Tests for health endpoints."""

from __future__ import annotations


def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
    # The sidebar builds its release label from version + channel.
    assert "channel" in data


def test_health_detailed(client):
    resp = client.get("/api/health/detailed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "db_ok" in data
    assert "version" in data
