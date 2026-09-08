"""The Anthropic output ceiling comes from the model, not from a constant.

A fixed 8192 was the value every Messages-API model accepts, so it never
errored — it just truncated every current model at a sixteenth of its real
capacity, and on a reasoning model the cap could be spent on thinking before
any visible content appeared. The ceiling is now read from the model's own
entry in the Models API, which also means there is no table here to go stale
as models ship.

What must hold: an explicit setting always wins, a successful lookup is used
and cached, and every failure path still sends a usable number.
"""

from __future__ import annotations

import asyncio

import httpx

from llm import model_limits


def _run(coro):
    return asyncio.run(coro)


def _mock(monkeypatch, handler):
    """Route the module's lookups to an in-memory transport."""
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr(model_limits.httpx, "AsyncClient", factory)
    model_limits.reset_cache()


def test_uses_the_models_own_output_cap(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        seen["version"] = request.headers.get("anthropic-version")
        return httpx.Response(
            200,
            json={
                "id": "claude-opus-5",
                "display_name": "Claude Opus 5",
                "max_input_tokens": 1000000,
                "max_tokens": 128000,
            },
        )

    _mock(monkeypatch, handler)

    cap = _run(
        model_limits.anthropic_max_tokens(
            "https://api.anthropic.com", "sk-test", "claude-opus-5"
        )
    )

    assert cap == 128000
    assert seen["url"] == "https://api.anthropic.com/v1/models/claude-opus-5"
    assert seen["key"] == "sk-test"
    assert seen["version"] == "2023-06-01"


def test_an_older_models_smaller_cap_is_respected(monkeypatch):
    """The point of asking is that the answer is not always the biggest number."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "old", "max_tokens": 4096})

    _mock(monkeypatch, handler)
    cap = _run(model_limits.anthropic_max_tokens("https://api", "k", "old"))
    assert cap == 4096


def test_one_lookup_per_model_however_many_turns_start_at_once(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"max_tokens": 128000})

    _mock(monkeypatch, handler)

    async def scenario():
        results = await asyncio.gather(
            *(
                model_limits.anthropic_max_tokens("https://api", "k", "m")
                for _ in range(5)
            )
        )
        assert results == [128000] * 5
        # And a later turn is served from the cache too.
        assert await model_limits.anthropic_max_tokens("https://api", "k", "m") == 128000

    _run(scenario())
    assert len(calls) == 1


def test_each_model_is_looked_up_separately(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        cap = 128000 if "opus" in str(request.url) else 8192
        return httpx.Response(200, json={"max_tokens": cap})

    _mock(monkeypatch, handler)

    assert _run(model_limits.anthropic_max_tokens("https://api", "k", "opus")) == 128000
    assert _run(model_limits.anthropic_max_tokens("https://api", "k", "legacy")) == 8192


def test_an_endpoint_without_the_field_falls_back_to_the_floor(monkeypatch):
    """A deployment predating the field must not send max_tokens: null."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "m", "display_name": "M"})

    _mock(monkeypatch, handler)
    cap = _run(model_limits.anthropic_max_tokens("https://api", "k", "m"))
    assert cap == model_limits.FLOOR_MAX_TOKENS


def test_a_nonsense_value_falls_back_to_the_floor(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"max_tokens": "lots"})

    _mock(monkeypatch, handler)
    assert (
        _run(model_limits.anthropic_max_tokens("https://api", "k", "m"))
        == model_limits.FLOOR_MAX_TOKENS
    )


def test_an_unreachable_or_rejecting_provider_falls_back_to_the_floor(monkeypatch):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    _mock(monkeypatch, refuse)
    assert (
        _run(model_limits.anthropic_max_tokens("https://api", "k", "m"))
        == model_limits.FLOOR_MAX_TOKENS
    )

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    _mock(monkeypatch, unauthorized)
    assert (
        _run(model_limits.anthropic_max_tokens("https://api", "k", "m"))
        == model_limits.FLOOR_MAX_TOKENS
    )


def test_a_failed_lookup_is_not_retried_every_turn(monkeypatch):
    """A provider that cannot answer is asked once, not once per message."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404, json={"error": "no such model"})

    _mock(monkeypatch, handler)
    for _ in range(4):
        assert (
            _run(model_limits.anthropic_max_tokens("https://api", "k", "m"))
            == model_limits.FLOOR_MAX_TOKENS
        )
    assert len(calls) == 1


def test_no_model_means_no_lookup(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"max_tokens": 128000})

    _mock(monkeypatch, handler)
    assert _run(model_limits.anthropic_max_tokens("https://api", "k", "")) == (
        model_limits.FLOOR_MAX_TOKENS
    )
    assert calls == []
