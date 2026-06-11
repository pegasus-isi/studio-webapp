"""Tests for tools/marketplace endpoints."""

from __future__ import annotations


def test_list_tools(client):
    resp = client.get("/api/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
    assert len(data["tools"]) >= 1
    # Check structure
    tool = data["tools"][0]
    assert "info" in tool
    assert "installed" in tool
    assert tool["info"]["id"] is not None


def test_get_tool(client):
    resp = client.get("/api/tools/claude-code")
    assert resp.status_code == 200
    data = resp.json()
    assert data["info"]["id"] == "claude-code"
    assert data["info"]["name"] == "Claude Code"
    assert data["installed"] is False


def test_get_tool_not_found(client):
    resp = client.get("/api/tools/nonexistent-tool")
    assert resp.status_code == 404


def test_tool_status(client):
    resp = client.get("/api/tools/claude-code/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tool_id"] == "claude-code"
    assert data["running"] is False


def test_settings(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "installed_tools" in data
    assert isinstance(data["installed_tools"], list)
