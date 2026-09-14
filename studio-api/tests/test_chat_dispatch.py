"""Tests for the chat OpenAI/Responses transport selection (_openai_dispatch)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

import routers.chat as chat


def test_is_responses_model():
    assert chat._is_responses_model("gpt-5.5")
    assert chat._is_responses_model("gpt-5")
    assert chat._is_responses_model("openai/o3-mini")
    assert not chat._is_responses_model("gpt-4o")
    assert not chat._is_responses_model("claude-sonnet-4-5")
    assert not chat._is_responses_model("")


def _http_error(status: int, text: str = "bad") -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://endpoint/chat/completions")
    resp = httpx.Response(status, request=req, text=text)
    return httpx.HTTPStatusError("err", request=req, response=resp)


def _patch(monkeypatch, chat_outcome):
    """Record which transport ran; chat_outcome is an Exception to raise or None.

    The transports are now streaming async generators; the fakes mirror that —
    they record the call then either raise (before the first yield, matching a
    handshake failure) or yield a single result event.
    """
    calls: list[str] = []

    async def fake_chat(*a, **k):
        calls.append("chat")
        if isinstance(chat_outcome, Exception):
            raise chat_outcome
        yield {"type": "result", "result": {"content": "chat", "tool_calls": [], "usage": {}}}

    async def fake_responses(*a, **k):
        calls.append("responses")
        yield {"type": "result", "result": {"content": "responses", "tool_calls": [], "usage": {}}}

    monkeypatch.setattr(chat, "_openai_stream", fake_chat)
    monkeypatch.setattr(chat, "_openai_responses_stream", fake_responses)
    chat._RESPONSES_ENDPOINTS.clear()  # avoid cross-test leakage
    return calls


def _dispatch(model, wire_api="auto", base_url="http://endpoint/v1"):
    async def run():
        out = []
        async for ev in chat._openai_dispatch(
            None, base_url, "key", model, [], "sys", wire_api
        ):
            out.append(ev)
        return out

    return asyncio.run(run())


def test_wire_api_responses_forced(monkeypatch):
    calls = _patch(monkeypatch, None)
    _dispatch("gpt-4o", wire_api="responses")
    assert calls == ["responses"]


def test_wire_api_chat_forced(monkeypatch):
    calls = _patch(monkeypatch, None)
    _dispatch("gpt-4o", wire_api="chat")
    assert calls == ["chat"]


def test_auto_responses_model_skips_chat(monkeypatch):
    calls = _patch(monkeypatch, None)
    _dispatch("gpt-5.5")  # responses-only model -> straight to /responses
    assert calls == ["responses"]


def test_auto_chat_model_uses_chat(monkeypatch):
    calls = _patch(monkeypatch, None)
    _dispatch("gpt-4o")
    assert calls == ["chat"]


def test_auto_falls_back_on_404(monkeypatch):
    calls = _patch(monkeypatch, _http_error(404))
    _dispatch("gpt-4o")
    assert calls == ["chat", "responses"]


def test_auto_falls_back_on_connection_error(monkeypatch):
    calls = _patch(monkeypatch, httpx.ConnectError("network error"))
    _dispatch("gpt-4o")
    assert calls == ["chat", "responses"]


def test_auto_falls_back_on_400_hint(monkeypatch):
    calls = _patch(monkeypatch, _http_error(400, "Unsupported parameter: 'messages'"))
    _dispatch("gpt-4o")
    assert calls == ["chat", "responses"]


def test_auto_reraises_unrelated_400(monkeypatch):
    calls = _patch(monkeypatch, _http_error(400, "you are out of credits"))
    with pytest.raises(httpx.HTTPStatusError):
        _dispatch("gpt-4o")
    assert calls == ["chat"]  # no fallback on an unrelated 400



def test_memo_is_per_model_not_per_endpoint(monkeypatch):
    """One model's wire API must not decide another's on a shared proxy.

    The FABRIC, NRP and RENCI proxies front many models on one base URL, each
    speaking its own wire API. While the memo was keyed by base URL alone, one
    model discovering it needs the Responses API pinned *every* model behind
    that URL to a Responses-shaped body — which a chat-completions gateway
    answers with `BadRequest - chat-completions request must have at least one
    message`, at turn one, having read nothing.
    """
    calls = _patch(monkeypatch, _http_error(400, "Unsupported parameter: 'messages'"))
    _dispatch("custom-reasoner-1")
    assert calls == ["chat", "responses"]      # probes, learns, falls back

    calls.clear()
    _dispatch("custom-reasoner-1")
    assert calls == ["responses"]              # same model: the memo skips the probe

    calls.clear()
    _dispatch("glm-5")
    assert calls == ["chat", "responses"]      # another model still gets its own probe
