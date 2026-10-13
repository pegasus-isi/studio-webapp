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
    # Keys are write-only (SEC-3): responses report presence, never the value.
    assert data["has_api_key"] is True
    assert "sk-test-key" not in resp.text

    # Verify it persists
    resp2 = client.get("/api/llm/config")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["provider"] == "anthropic"
    assert data2["has_api_key"] is True
    assert "sk-test-key" not in resp2.text


def test_list_providers(client):
    resp = client.get("/api/llm/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    provider_ids = [p["id"] for p in data["providers"]]
    assert "gateway" in provider_ids
    assert "anthropic" in provider_ids
    assert "openai" in provider_ids
    # FABRIC and NRP were presets with hardcoded URLs and per-site key names.
    # Both are ordinary OpenAI-compatible endpoints, so they are added as custom
    # endpoints now and no longer take up room in the built-in list.
    assert "fabric" not in provider_ids
    assert "nrp" not in provider_ids


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
    resp = client.put("/api/llm/config", json={"provider": "anthropic"})
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
    assert data["has_api_key"] is True
    assert data["model"] == "claude-opus-4-6"


def test_upsert_saves_a_keyless_preset_as_an_inactive_draft(client):
    """Adding a provider must not require the key up front.

    The key field lives in the provider's card, and the card only renders once
    the row exists — so rejecting the create made Anthropic, OpenAI and Google
    impossible to add at all ("unable to add"). The row is saved instead, and
    kept out of service until it is usable.
    """
    resp = client.put(
        "/api/llm/provider-configs",
        json={"provider_id": "anthropic", "name": "Anthropic", "api_key": "",
              "is_active": True},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False   # asked for, refused: unusable

    # And it cannot be pressed into service while still keyless.
    resp = client.post("/api/llm/provider-configs/anthropic/activate")
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
    # Saved, but not usable: the endpoint demands a key this row does not have.
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    resp = client.post("/api/llm/provider-configs/custom-x/activate")
    assert resp.status_code == 400


# --- SEC-3: keys are write-only ---


def test_provider_configs_never_echo_keys(client):
    _add_provider(client, "custom-z", active=False)
    resp = client.get("/api/llm/provider-configs")
    assert resp.status_code == 200
    cfg = next(
        c for c in resp.json()["configs"] if c["provider_id"] == "custom-z"
    )
    assert cfg["has_api_key"] is True
    assert "api_key" not in cfg

    # GET /api/settings serializes the same model — no key there either.
    _add_provider(client, "custom-z", active=True)
    llm = client.get("/api/settings").json()["llm"]
    assert llm["has_api_key"] is True
    assert "api_key" not in llm


def test_saved_provider_models_uses_stored_key(client, monkeypatch):
    """The chat model chooser lists a saved provider's models WITHOUT the
    browser ever holding the key — resolved server-side from the DB."""
    _add_provider(client, "custom-m", base="https://m.example/v1")

    seen = {}

    async def fake_fetch(base_url, api_key):
        seen["base_url"] = base_url
        seen["api_key"] = api_key
        return ["m1", "m2"], None

    monkeypatch.setattr("routers.llm.fetch_models", fake_fetch)
    resp = client.get("/api/llm/provider-configs/custom-m/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["models"] == ["m1", "m2"]
    assert seen == {"base_url": "https://m.example/v1", "api_key": "k"}


def test_saved_provider_models_without_saved_row_uses_preset(client, monkeypatch):
    """No saved row falls back to preset defaults with no key (keyless
    providers like the gateway still get model discovery in chat)."""
    seen = {}

    async def fake_fetch(base_url, api_key):
        seen["base_url"] = base_url
        seen["api_key"] = api_key
        return ["default"], None

    monkeypatch.setattr("routers.llm.fetch_models", fake_fetch)
    resp = client.get("/api/llm/provider-configs/gateway/models")
    assert resp.status_code == 200
    assert resp.json()["valid"] is True
    assert seen["api_key"] == ""
    assert seen["base_url"]  # preset default resolved server-side


def test_custom_provider_can_be_added_before_it_is_configured(client):
    """A custom endpoint is created empty and configured in its card.

    Validating the create deadlocked it: the field supplying the base URL only
    renders once a row exists, and the row could not be saved without the base
    URL — so custom endpoints could not be added at all.
    """
    resp = client.put("/api/llm/provider-configs", json={
        "provider_id": "custom-abc123",
        "name": "Custom Endpoint",
        "base_url": "",
        "default_model": "",
        "api_key": "",
        "is_active": True,
    })
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["provider_id"] == "custom-abc123"
    # Never active while unconfigured: chat would point at an endpoint that
    # cannot answer.
    assert body["is_active"] is False


def test_an_unconfigured_custom_provider_cannot_be_activated(client):
    client.put("/api/llm/provider-configs", json={
        "provider_id": "custom-def456", "name": "Draft",
        "base_url": "", "default_model": "", "api_key": "", "is_active": False,
    })

    resp = client.post("/api/llm/provider-configs/custom-def456/activate")
    assert resp.status_code == 400
    assert "base URL" in resp.json()["detail"]


def test_a_keyless_preset_never_becomes_the_active_provider(client):
    """Saving freely must not let chat point at a provider that cannot answer."""
    resp = client.put("/api/llm/provider-configs", json={
        "provider_id": "google", "name": "Google", "base_url": "",
        "default_model": "", "api_key": "", "is_active": True,
    })
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Supplying the key makes it activatable, with no re-adding needed.
    resp = client.put("/api/llm/provider-configs", json={
        "provider_id": "google", "name": "Google", "base_url": "",
        "default_model": "", "api_key": "k", "is_active": True,
    })
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


# --- activation must not discard extra_config ---


def test_activating_a_provider_keeps_extra_config(client):
    """extra_config survives activation.

    It is global (one llm_config row), and activation used to write it as {},
    so switching provider silently discarded wire_api / max_tokens. Activation
    is routine, so the loss looked like the setting never applying at all.
    """
    _add_provider(client, "custom-abc", active=True)
    client.put(
        "/api/llm/config",
        json={
            "provider": "custom",
            "model": "m",
            "api_key": "k",
            "base_url": "https://x/v1",
            "extra_config": {"max_tokens": 16384, "wire_api": "chat"},
        },
    )

    _add_provider(client, "custom-def", active=False)
    assert client.post("/api/llm/provider-configs/custom-def/activate").status_code == 200

    extra = client.get("/api/llm/config").json()["extra_config"]
    assert extra.get("max_tokens") == 16384
    assert extra.get("wire_api") == "chat"


# ── No gateway to default to (#498) ──
#
# On an Open OnDemand cluster the hosted deployment's gateway.llm does not
# exist. An explicitly empty STUDIO_LLM_GATEWAY_URL means "no gateway": nothing
# is configured until the user adds a provider with their own key.


def _no_gateway(monkeypatch):
    import llm.providers as p

    monkeypatch.setattr(p, "GATEWAY_BASE_URL", "")


def test_without_a_gateway_nothing_is_configured(client, monkeypatch):
    _no_gateway(monkeypatch)
    resp = client.get("/api/llm/config")
    assert resp.status_code == 200
    assert resp.json()["provider"] == ""


def test_without_a_gateway_it_is_not_offered(client, monkeypatch):
    _no_gateway(monkeypatch)
    ids = [p["id"] for p in client.get("/api/llm/providers").json()["providers"]]
    assert "gateway" not in ids
    assert "anthropic" in ids


def test_without_a_gateway_deleting_the_last_provider_clears_it(client, monkeypatch):
    """Not a fallback to a host that does not exist."""
    _no_gateway(monkeypatch)
    assert _add_provider(client, "custom-abc", active=True).status_code == 200
    assert client.get("/api/llm/config").json()["provider"] == "custom-abc"
    assert client.delete("/api/llm/provider-configs/custom-abc").status_code == 200
    assert client.get("/api/llm/config").json()["provider"] == ""


def test_a_users_own_provider_works_without_a_gateway(client, monkeypatch):
    _no_gateway(monkeypatch)
    resp = client.put(
        "/api/llm/config",
        json={"provider": "anthropic", "model": "claude-sonnet-4-5-20250929", "api_key": "sk-x"},
    )
    assert resp.status_code == 200
    cfg = client.get("/api/llm/config").json()
    assert cfg["provider"] == "anthropic" and cfg["has_api_key"] is True


def test_without_a_gateway_a_saved_provider_is_offered_not_activated(client, monkeypatch):
    """#504: saving a provider does not make it the default; it is listed in
    the chat's model picker (via the engine config) for the user to choose."""
    _no_gateway(monkeypatch)
    assert _add_provider(client, "custom-az", active=False).status_code == 200
    assert client.get("/api/llm/config").json()["provider"] == ""
    ids = [c["provider_id"] for c in client.get("/api/llm/provider-configs").json()["configs"]]
    assert "custom-az" in ids


def test_with_a_gateway_saving_a_provider_does_not_steal_active(client):
    """Hosted: the gateway default stays active until the user says otherwise."""
    assert _add_provider(client, "custom-az", active=False).status_code == 200
    assert client.get("/api/llm/config").json()["provider"] == "gateway"
