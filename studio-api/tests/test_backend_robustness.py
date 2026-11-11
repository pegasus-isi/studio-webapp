"""Tests for the Aug-2026 backend robustness fixes (BE-4, BE-5, BE-8)."""

from __future__ import annotations


# --- BE-4: _trim_history keeps a provider-valid boundary ---


# --- BE-8: run graph workflow_status derived from node statuses ---


def test_derive_workflow_status():
    from routers.workflows import _derive_workflow_status

    assert _derive_workflow_status([]) is None
    assert _derive_workflow_status(
        [{"status": "unsubmitted"}, {"status": "unsubmitted"}]
    ) is None
    assert _derive_workflow_status(
        [{"status": "succeeded"}, {"status": "running"}]
    ) == "running"
    assert _derive_workflow_status(
        [{"status": "succeeded"}, {"status": "failed"}]
    ) == "failed"
    assert _derive_workflow_status(
        [{"status": "succeeded"}, {"status": "succeeded"}]
    ) == "succeeded"
    # Active beats failed.
    assert _derive_workflow_status(
        [{"status": "failed"}, {"status": "running"}]
    ) == "running"


# --- BE-5: legacy PUT /api/settings goes through the guarded save path ---


def test_settings_put_rejects_keyless_required_provider(client):
    r = client.put("/api/settings", json={"provider": "anthropic"})
    assert r.status_code == 400
    assert "API key" in r.json()["detail"]
    # Nothing saved — still the gateway default.
    assert client.get("/api/llm/config").json()["provider"] == "gateway"


def test_settings_put_omitted_key_keeps_stored_key(client):
    # Seed a keyed provider via the llm route.
    assert client.put(
        "/api/llm/config",
        json={"provider": "anthropic", "api_key": "sk-settings-keep"},
    ).status_code == 200
    # A model-only save via the LEGACY settings route must not wipe the key.
    r = client.put(
        "/api/settings",
        json={"provider": "anthropic", "model": "claude-opus-4-6"},
    )
    assert r.status_code == 200
    cfg = client.get("/api/llm/config").json()
    assert cfg["has_api_key"] is True
    assert cfg["model"] == "claude-opus-4-6"
