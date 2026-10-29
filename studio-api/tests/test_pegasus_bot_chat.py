"""Tests for the Pegasus bot chat round adapter (_pegasus_chat_stream)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

import routers.chat as chat


class _StubClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = responses
        self.calls = 0

    async def post(self, url: str, json=None, timeout=None):  # noqa: ANN001, ANN201, ARG002
        self.calls += 1
        return self._responses.pop(0)


def _response(status: int, **kwargs) -> httpx.Response:
    return httpx.Response(
        status,
        request=httpx.Request("POST", "http://bot/chat"),
        **kwargs,
    )


def test_bot_empty_response_is_retried_once() -> None:
    async def run():
        client = _StubClient([
            _response(502, text='{"detail":"EMPTY_MODEL_RESPONSE"}'),
            _response(
                200,
                json={
                    "status_code": 200,
                    "status": "successful",
                    "response": "ok",
                    "tool_calls": [],
                    "finish_reason": "stop",
                },
            ),
        ])
        events = [
            e
            async for e in chat._pegasus_chat_stream(
                client, "http://bot", [], [], "u", "r", None, "sys"
            )
        ]
        return client, events

    client, events = asyncio.run(run())
    assert client.calls == 2
    results = [e for e in events if e["type"] == "result"]
    assert results and results[0]["result"]["content"] == "ok"


def test_bot_empty_response_fails_after_retry() -> None:
    async def run():
        client = _StubClient([
            _response(502, text='{"detail":"EMPTY_MODEL_RESPONSE"}'),
            _response(502, text='{"detail":"EMPTY_MODEL_RESPONSE"}'),
        ])
        return [
            e
            async for e in chat._pegasus_chat_stream(
                client, "http://bot", [], [], "u", "r", None, "sys"
            )
        ]

    with pytest.raises(RuntimeError, match="empty response after a retry"):
        asyncio.run(run())
