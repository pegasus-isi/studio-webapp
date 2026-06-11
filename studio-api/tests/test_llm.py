"""Tests for LLM configuration endpoints."""

from __future__ import annotations


def test_get_llm_config_default(client):
    resp = client.get("/api/llm/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "anthropic"


def test_put_llm_config(client):
    resp = client.put(
        "/api/llm/config",
        json={
            "provider": "anthropic",
            "model": "claude-sonnet-4-5-20250929",
            "api_key": "sk-test-key",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "anthropic"
    assert data["model"] == "claude-sonnet-4-5-20250929"
    assert data["api_key"] == "sk-test-key"

    # Verify it persists
    resp2 = client.get("/api/llm/config")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["provider"] == "anthropic"
    assert data2["api_key"] == "sk-test-key"


def test_list_providers(client):
    resp = client.get("/api/llm/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    provider_ids = [p["id"] for p in data["providers"]]
    assert "anthropic" in provider_ids
    assert "openai" in provider_ids
    assert "fabric" in provider_ids
