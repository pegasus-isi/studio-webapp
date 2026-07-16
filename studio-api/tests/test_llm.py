"""Tests for LLM configuration endpoints."""

from __future__ import annotations


def test_get_llm_config_default(client):
    resp = client.get("/api/llm/config")
    assert resp.status_code == 200
    data = resp.json()
    # Ships pointed at the internal gateway with zero configuration.
    assert data["provider"] == "gateway"
    assert data["model"] == "Default"
    assert data["base_url"] == "http://gateway.llm:8080/v1"


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
    assert "gateway" in provider_ids
    assert "anthropic" in provider_ids
    assert "openai" in provider_ids
    assert "fabric" in provider_ids


def _add_provider(client, pid, active=False, base="https://x/v1"):
    return client.put(
        "/api/llm/provider-configs",
        json={
            "provider_id": pid, "name": pid, "api_key": "k",
            "base_url": base, "default_model": "m", "is_active": active,
        },
    )


def test_delete_active_provider_falls_back_to_gateway(client):
    # A dangling active config on a deleted endpoint is exactly what broke chat
    # (removed custom provider, but llm_config still pointed at it).
    assert _add_provider(client, "custom-abc", active=True).status_code == 200
    assert client.get("/api/llm/config").json()["provider"] == "custom-abc"

    assert client.delete("/api/llm/provider-configs/custom-abc").status_code == 200
    cfg = client.get("/api/llm/config").json()
    assert cfg["provider"] == "gateway"
    assert cfg["base_url"] == "http://gateway.llm:8080/v1"


def test_delete_active_provider_falls_back_to_remaining(client):
    _add_provider(client, "p1", active=False)
    _add_provider(client, "p2", active=True)
    assert client.get("/api/llm/config").json()["provider"] == "p2"

    client.delete("/api/llm/provider-configs/p2")
    assert client.get("/api/llm/config").json()["provider"] == "p1"


def test_delete_inactive_provider_keeps_active(client):
    _add_provider(client, "keep", active=True)
    _add_provider(client, "drop", active=False)
    client.delete("/api/llm/provider-configs/drop")
    assert client.get("/api/llm/config").json()["provider"] == "keep"
