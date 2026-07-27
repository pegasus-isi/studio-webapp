"""Tests for LLM configuration endpoints."""

from __future__ import annotations


def test_get_llm_config_default(client):
    resp = client.get("/api/llm/config")
    assert resp.status_code == 200
    data = resp.json()
    # Ships pointed at the internal gateway with zero configuration. The model
    # must be lowercase "default" — the gateway is case-sensitive and rejects
    # "Default" with "could not auto resolve a provider".
    assert data["provider"] == "gateway"
    assert data["model"] == "default"
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


# --- API-key requirement at save time ---
# A keyless fabric config used to save fine and only fail at chat time, deep
# inside opencode, as "Authentication Error, No api key passed in".


def test_put_llm_config_keyed_preset_without_key_rejected(client):
    resp = client.put("/api/llm/config", json={"provider": "fabric"})
    assert resp.status_code == 400
    assert "API key" in resp.json()["detail"]
    # Nothing was saved — still on the gateway default.
    assert client.get("/api/llm/config").json()["provider"] == "gateway"


def test_put_llm_config_gateway_needs_no_key(client):
    resp = client.put("/api/llm/config", json={"provider": "gateway"})
    assert resp.status_code == 200


def test_put_llm_config_omitted_key_keeps_stored_key(client):
    client.put(
        "/api/llm/config",
        json={"provider": "anthropic", "api_key": "sk-keep-me"},
    )
    # Model-only update without resending the key must not wipe (or reject) it.
    resp = client.put(
        "/api/llm/config",
        json={"provider": "anthropic", "model": "claude-opus-4-6"},
    )
    assert resp.status_code == 200
    data = client.get("/api/llm/config").json()
    assert data["api_key"] == "sk-keep-me"
    assert data["model"] == "claude-opus-4-6"


def test_upsert_provider_config_keyed_preset_without_key_rejected(client):
    resp = client.put(
        "/api/llm/provider-configs",
        json={"provider_id": "fabric", "name": "FABRIC AI", "api_key": ""},
    )
    assert resp.status_code == 400
    assert "API key" in resp.json()["detail"]


def test_custom_provider_keyless_allowed_unless_endpoint_demands_auth(
    client, monkeypatch
):
    import llm.providers as providers

    # Endpoint unreachable/no auth demanded -> keyless custom is allowed
    # (self-hosted endpoints may legitimately have no key).
    async def _no_auth(base_url, api_key):
        return [], "Failed to fetch models: unreachable"

    monkeypatch.setattr(providers, "fetch_models", _no_auth)
    resp = client.put(
        "/api/llm/provider-configs",
        json={"provider_id": "custom-x", "name": "x", "base_url": "https://x/v1"},
    )
    assert resp.status_code == 200

    # Endpoint rejects keyless requests -> save and activate are both rejected.
    async def _auth_required(base_url, api_key):
        return [], "HTTP 401: Unauthorized"

    monkeypatch.setattr(providers, "fetch_models", _auth_required)
    resp = client.put(
        "/api/llm/provider-configs",
        json={"provider_id": "custom-y", "name": "y", "base_url": "https://y/v1"},
    )
    assert resp.status_code == 400
    assert "API key" in resp.json()["detail"]

    resp = client.post("/api/llm/provider-configs/custom-x/activate")
    assert resp.status_code == 400
