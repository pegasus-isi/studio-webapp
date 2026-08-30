"""Tests for the output-token ceiling (extra_config.max_tokens).

A fixed ceiling truncates reasoning models — their thinking tokens are billed
against the same budget, so the cap can be spent before any content or tool
call is emitted. The OpenAI transports therefore omit the field unless it is
configured; the Anthropic Messages API requires it and falls back to a default.
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

def test_anthropic_falls_back_to_default():
    sent = _capture(chat._anthropic_stream)
    assert sent["max_tokens"] == chat._ANTHROPIC_DEFAULT_MAX_TOKENS


def test_anthropic_sends_configured_max_tokens():
    assert _capture(chat._anthropic_stream, max_tokens=64000)["max_tokens"] == 64000


# ── operator-supplied config is free-form JSON ───────────────────────────

def test_coerce_max_tokens_accepts_positive_int_and_numeric_string():
    assert chat._coerce_max_tokens(4096, "p") == 4096
    assert chat._coerce_max_tokens("4096", "p") == 4096


def test_coerce_max_tokens_drops_unusable_values():
    for bad in (None, 0, -1, "", "lots", [], {}):
        assert chat._coerce_max_tokens(bad, "p") is None
