"""Tests for the Aug-2026 backend robustness fixes (BE-4, BE-5, BE-8)."""

from __future__ import annotations


# --- BE-4: _trim_history keeps a provider-valid boundary ---


def test_trim_history_short_is_unchanged():
    from routers.chat import _trim_history

    msgs = [{"role": "user", "content": "hi"}]
    assert _trim_history(msgs) == msgs


def test_trim_history_starts_on_a_genuine_user_turn():
    from routers.chat import _MAX_HISTORY_MESSAGES, _trim_history

    # Alternating u/a, ending on the new user ask. A blind tail slice would
    # start on an assistant message (Anthropic 400s); the trim must advance to
    # a user turn.
    msgs = []
    for i in range(_MAX_HISTORY_MESSAGES + 5):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"m{i}"})
    msgs.append({"role": "user", "content": "current ask"})

    out = _trim_history(msgs)
    assert out[0]["role"] == "user"
    assert isinstance(out[0]["content"], str)
    assert out[-1]["content"] == "current ask"
    assert len(out) <= _MAX_HISTORY_MESSAGES


def test_trim_history_skips_tool_result_carrier_at_boundary():
    from routers.chat import _MAX_HISTORY_MESSAGES, _trim_history

    # Fill so the raw tail would begin on an Anthropic tool-result carrier
    # (role user, list content) whose matching assistant tool_use is trimmed.
    msgs = [{"role": "user", "content": f"pad{i}"} for i in range(_MAX_HISTORY_MESSAGES)]
    msgs += [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1"}]},
        {"role": "user", "content": "current ask"},
    ]
    out = _trim_history(msgs)
    # First kept message must be a plain-string user turn, never the list-content
    # tool-result carrier.
    assert out[0]["role"] == "user"
    assert isinstance(out[0]["content"], str)


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
