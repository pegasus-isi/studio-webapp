"""Tests for the output-token ceiling (extra_config.max_tokens).

A fixed ceiling truncates reasoning models — their thinking tokens are billed
against the same budget, so the cap can be spent before any content or tool
call is emitted. The OpenAI transports therefore omit the field unless it is
configured; the Anthropic Messages API requires one, and resolves it from the
selected model (llm/model_limits.py, tests/test_model_limits.py) rather than
from a constant. What is asserted here is the precedence: an explicit setting
wins over anything discovered, and the transport itself still has a floor for a
direct call that passes nothing.
"""

from __future__ import annotations

import asyncio
import json

import httpx

import routers.chat as chat


def _capture(fn, **kwargs) -> dict:
    """Run one stream generator against a mock transport, return the sent body."""
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, text="")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async for _ in fn(client, "http://endpoint", "key", "m", [], "sys", **kwargs):
                pass

    asyncio.run(run())
    return sent


# ── OpenAI Chat Completions ──────────────────────────────────────────────

def test_openai_omits_max_tokens_when_unset():
    assert "max_tokens" not in _capture(chat._openai_stream)


def test_openai_sends_configured_max_tokens():
    assert _capture(chat._openai_stream, max_tokens=32000)["max_tokens"] == 32000


# ── OpenAI Responses ─────────────────────────────────────────────────────

def test_responses_omits_max_output_tokens_when_unset():
    assert "max_output_tokens" not in _capture(chat._openai_responses_stream)


def test_responses_sends_configured_max_tokens():
    sent = _capture(chat._openai_responses_stream, max_tokens=32000)
    assert sent["max_output_tokens"] == 32000


# ── Anthropic Messages (max_tokens is required by the API) ───────────────

def test_anthropic_transport_has_a_floor_of_its_own():
    """The transport must always send a number, even called with none.

    The ceiling normally arrives resolved from the model. This is the guard for
    a direct call that resolved nothing: the Messages API rejects a request
    without max_tokens, so a None here would be a failed turn.
    """
    sent = _capture(chat._anthropic_stream)
    assert sent["max_tokens"] == chat.FLOOR_MAX_TOKENS


def test_anthropic_sends_configured_max_tokens():
    assert _capture(chat._anthropic_stream, max_tokens=64000)["max_tokens"] == 64000


# ── operator-supplied config is free-form JSON ───────────────────────────

def test_coerce_max_tokens_accepts_positive_int_and_numeric_string():
    assert chat._coerce_max_tokens(4096, "p") == 4096
    assert chat._coerce_max_tokens("4096", "p") == 4096


def test_coerce_max_tokens_drops_unusable_values():
    for bad in (None, 0, -1, "", "lots", [], {}):
        assert chat._coerce_max_tokens(bad, "p") is None


# ── per-request override (body wins over the global stored config) ────────

def _stream_body(monkeypatch, request_body: dict, stored: dict | None = None) -> dict:
    """POST /api/chat/stream and return the max_tokens the transport received."""
    from fastapi.testclient import TestClient
    import main

    seen: dict = {}

    async def fake_dispatch(client_, base_url, api_key, model, messages,
                            system_prompt, wire_api, max_tokens=None):
        seen["max_tokens"] = max_tokens
        yield {"type": "text", "text": "ok"}
        yield {"type": "result",
               "result": {"content": "ok", "tool_calls": [], "usage": {}}}

    monkeypatch.setattr(chat, "_openai_dispatch", fake_dispatch)

    async def fake_config():
        return {"provider": "custom-x", "model": "m", "api_key": "k",
                "base_url": "http://endpoint/v1", "extra_config": stored or {}}

    monkeypatch.setattr(main.db, "get_llm_config", fake_config)

    with TestClient(main.app) as client:
        client.post("/api/chat/stream", json={
            "messages": [{"role": "user", "content": "hi"}], **request_body})
    return seen


def test_request_max_tokens_overrides_stored(monkeypatch):
    seen = _stream_body(monkeypatch, {"max_tokens": 32000}, stored={"max_tokens": 8192})
    assert seen["max_tokens"] == 32000


def test_stored_max_tokens_used_when_request_omits_it(monkeypatch):
    seen = _stream_body(monkeypatch, {}, stored={"max_tokens": 8192})
    assert seen["max_tokens"] == 8192


def test_no_ceiling_when_neither_is_set(monkeypatch):
    assert _stream_body(monkeypatch, {}, stored={})["max_tokens"] is None


def test_bad_request_max_tokens_falls_back_to_no_ceiling(monkeypatch):
    assert _stream_body(monkeypatch, {"max_tokens": "lots"}, stored={})["max_tokens"] is None


# ── the turn resolves the ceiling from the model ─────────────────────────

def _anthropic_turn(monkeypatch, stored: dict | None = None, resolved: int = 128000):
    """Run one Anthropic turn, returning the max_tokens the transport was given."""
    import main
    from fastapi.testclient import TestClient

    seen: dict = {}

    async def fake_resolver(base_url, api_key, model):
        seen["resolver"] = {"base_url": base_url, "model": model}
        return resolved

    async def fake_stream(client, base_url, api_key, model, messages, system_prompt,
                          max_tokens=None):
        seen["max_tokens"] = max_tokens
        yield {"type": "result", "result": {"content": "ok", "tool_calls": [],
                                            "usage_in": 1, "usage_out": 1,
                                            "stop_reason": "end_turn"}}

    monkeypatch.setattr(chat, "anthropic_max_tokens", fake_resolver)
    monkeypatch.setattr(chat, "_anthropic_stream", fake_stream)

    async def fake_config():
        return {"provider": "anthropic", "model": "claude-opus-5", "api_key": "k",
                "base_url": "", "extra_config": stored or {}}

    monkeypatch.setattr(main.db, "get_llm_config", fake_config)

    with TestClient(main.app) as client:
        client.post("/api/chat/stream", json={
            "messages": [{"role": "user", "content": "hi"}]})
    return seen


def test_turn_asks_the_model_for_its_ceiling(monkeypatch):
    """With nothing configured, the ceiling is the selected model's own.

    This is the whole point: 8192 never errored, it just cut every current
    model off at a sixteenth of what it can produce.
    """
    seen = _anthropic_turn(monkeypatch)
    assert seen["max_tokens"] == 128000
    assert seen["resolver"]["model"] == "claude-opus-5"
    # Resolved against the provider's own endpoint, defaulted for Anthropic.
    assert seen["resolver"]["base_url"] == "https://api.anthropic.com"


def test_configured_ceiling_wins_over_the_models_own(monkeypatch):
    """An operator who set a number meant it — don't discover over the top."""
    seen = _anthropic_turn(monkeypatch, stored={"max_tokens": 4096})
    assert seen["max_tokens"] == 4096
    assert "resolver" not in seen
