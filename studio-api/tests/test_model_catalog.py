"""Reading per-model limits off an OpenAI-compatible /models listing.

The OpenAI spec carries no context window or output cap, and each proxy that
adds them picks its own field name. The parser must read every common
spelling and assume nothing when a server reports nothing.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from llm.providers import fetch_model_catalog, fetch_models, model_limits


@pytest.mark.parametrize(
    "entry, expected",
    [
        # LiteLLM: both on the plain listing
        ({"id": "x", "max_input_tokens": 272000, "max_output_tokens": 128000},
         {"context": 272000, "output": 128000}),
        # LiteLLM with nulls for a model it does not know
        ({"id": "x", "max_input_tokens": None, "max_output_tokens": None}, {}),
        # OpenRouter: context_length + nested top_provider cap
        ({"id": "x", "context_length": 200000,
          "top_provider": {"max_completion_tokens": 16384}},
         {"context": 200000, "output": 16384}),
        # Groq
        ({"id": "x", "context_window": 131072, "max_completion_tokens": 8192},
         {"context": 131072, "output": 8192}),
        # vLLM
        ({"id": "x", "max_model_len": 262144}, {"context": 262144}),
        # Mistral
        ({"id": "x", "max_context_length": 128000}, {"context": 128000}),
        # Anthropic's naming
        ({"id": "x", "max_input_tokens": 200000, "max_tokens": 64000},
         {"context": 200000, "output": 64000}),
        # Plain OpenAI entry: nothing to read
        ({"id": "x", "object": "model", "owned_by": "openai"}, {}),
        # Junk values read as absent, never as a limit
        ({"id": "x", "max_input_tokens": "128k", "max_output_tokens": 0,
          "context_length": True, "max_tokens": -1}, {}),
    ],
)
def test_model_limits_reads_each_dialect(entry, expected):
    assert model_limits(entry) == expected


def test_specific_field_wins_over_generic():
    """LiteLLM reports max_input_tokens next to a generic max_tokens; the
    context must come from the former and the output from the latter."""
    entry = {"id": "x", "max_input_tokens": 272000, "max_tokens": 128000}
    assert model_limits(entry) == {"context": 272000, "output": 128000}


def _serve(payload, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)


def test_catalog_carries_limits_and_ids_stay_sorted(monkeypatch):
    _serve(
        {"data": [
            {"id": "zeta", "max_input_tokens": 1000, "max_output_tokens": 10},
            {"id": "alpha"},
            {"object": "model"},  # no id: skipped
        ]},
        monkeypatch,
    )
    entries, err = asyncio.run(fetch_model_catalog("https://gw.example/v1", "k"))
    assert err is None
    assert entries == [
        {"id": "alpha", "limits": {}},
        {"id": "zeta", "limits": {"context": 1000, "output": 10}},
    ]
    # The id-only view used by the Settings model picker is unchanged.
    ids, err = asyncio.run(fetch_models("https://gw.example/v1", "k"))
    assert (ids, err) == (["alpha", "zeta"], None)


def test_catalog_empty_listing_is_an_error(monkeypatch):
    _serve({"data": []}, monkeypatch)
    entries, err = asyncio.run(fetch_model_catalog("https://gw.example/v1", ""))
    assert entries == [] and err == "Endpoint returned no models."
