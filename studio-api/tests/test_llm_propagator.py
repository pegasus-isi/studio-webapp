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
        return [
            {"id": "gpt-5.6-luna", "limits": {}},
            {"id": "gpt-5.6-sol", "limits": {}},
            {"id": "kimi-k2.6", "limits": {}},
        ], None

    monkeypatch.setattr(propagator, "fetch_model_catalog", fake_fetch)

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
    # Nothing reported a limit, so nothing is attached — not an empty map.
    assert "model_limits" not in providers[0]


def test_reported_limits_are_attached_per_model(monkeypatch):
    """A model the listing sizes gets its limits attached; one it does not
    gets no entry — the adapters must not see a guess for it."""
    from llm import propagator

    async def fake_fetch(base_url, api_key):
        return [
            {"id": "big", "limits": {"context": 262144, "output": 32768}},
            {"id": "ctx-only", "limits": {"context": 128000}},
            {"id": "unsized", "limits": {}},
        ], None

    monkeypatch.setattr(propagator, "fetch_model_catalog", fake_fetch)

    providers = [{"provider_id": "custom-gw", "base_url": "https://gw.example/v1"}]
    _run(propagator._attach_model_lists(providers))
    assert providers[0]["models"] == ["big", "ctx-only", "unsized"]
    assert providers[0]["model_limits"] == {
        "big": {"context": 262144, "output": 32768},
        "ctx-only": {"context": 128000},
    }


def test_fetch_failure_leaves_models_unset(monkeypatch):
    """A down endpoint degrades to default_model — it must never raise."""
    from llm import propagator

    async def fake_fetch(base_url, api_key):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(propagator, "fetch_model_catalog", fake_fetch)

    providers = [
        {"provider_id": "custom-down", "base_url": "https://down.example/v1"},
    ]
    _run(propagator._attach_model_lists(providers))
    assert "models" not in providers[0]


def test_hung_endpoint_is_bounded_by_timeout(monkeypatch):
    from llm import propagator

    async def hang(base_url, api_key):
        await asyncio.sleep(60)

    monkeypatch.setattr(propagator, "fetch_model_catalog", hang)
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

    monkeypatch.setattr(propagator, "fetch_model_catalog", fake_fetch)

    providers = [
        {"provider_id": "anthropic", "base_url": "https://api.anthropic.com/v1"},
        {"provider_id": "custom-draft", "base_url": ""},
    ]
    _run(propagator._attach_model_lists(providers))
    assert all("models" not in p for p in providers)
