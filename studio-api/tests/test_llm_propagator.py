"""Tests for model-list attachment in the LLM propagator."""

from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_fetched_models_are_attached(monkeypatch):
    from llm import propagator

    async def fake_fetch(base_url, api_key):
        assert base_url == "https://ai.example.org/v1"
        assert api_key == "sk-renci"
        return ["gpt-5.6-luna", "gpt-5.6-sol", "kimi-k2.6"], None

    monkeypatch.setattr(propagator, "fetch_models", fake_fetch)

    providers = [
        {
            "provider_id": "custom-renci",
            "api_key": "sk-renci",
            "base_url": "https://ai.example.org/v1",
            "default_model": "gpt-5.6-sol",
        },
    ]
    _run(propagator._attach_model_lists(providers))
    assert providers[0]["models"] == ["gpt-5.6-luna", "gpt-5.6-sol", "kimi-k2.6"]


def test_fetch_failure_leaves_models_unset(monkeypatch):
    """A down endpoint degrades to default_model — it must never raise."""
    from llm import propagator

    async def fake_fetch(base_url, api_key):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(propagator, "fetch_models", fake_fetch)

    providers = [
        {"provider_id": "custom-down", "base_url": "https://down.example/v1"},
    ]
    _run(propagator._attach_model_lists(providers))
    assert "models" not in providers[0]


def test_hung_endpoint_is_bounded_by_timeout(monkeypatch):
    from llm import propagator

    async def hang(base_url, api_key):
        await asyncio.sleep(60)

    monkeypatch.setattr(propagator, "fetch_models", hang)
    monkeypatch.setattr(propagator, "MODEL_FETCH_TIMEOUT", 0.05)

    providers = [
        {"provider_id": "custom-slow", "base_url": "https://slow.example/v1"},
    ]
    _run(propagator._attach_model_lists(providers))
    assert "models" not in providers[0]


def test_anthropic_and_urlless_providers_are_skipped(monkeypatch):
    """Anthropic has no OpenAI-style /models, and a draft without a base URL
    has nothing to probe — neither may trigger a fetch."""
    from llm import propagator

    async def fake_fetch(base_url, api_key):
        raise AssertionError("should not be called")

    monkeypatch.setattr(propagator, "fetch_models", fake_fetch)

    providers = [
        {"provider_id": "anthropic", "base_url": "https://api.anthropic.com/v1"},
        {"provider_id": "custom-draft", "base_url": ""},
    ]
    _run(propagator._attach_model_lists(providers))
    assert all("models" not in p for p in providers)
