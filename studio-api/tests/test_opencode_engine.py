"""Tests for the OpenCode chat engine (event translation + endpoints)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import services.opencode_engine as oce


def _fake_server(
    events: list[dict], final_text: str = "", prompts: list[dict] | None = None
) -> "httpx.MockTransport":
    """A fake `opencode serve` as an httpx MockTransport.

    *events* is the scripted /event SSE payload list (studio session events).
    *prompts* (when given) collects each /prompt_async request body.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/health":
            return httpx.Response(200, json={"healthy": True})
        if path == "/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "ses_test1"})
        if path == "/session/ses_test1" and request.method == "GET":
            return httpx.Response(200, json={"id": "ses_test1"})
        if path == "/agent":
            return httpx.Response(200, json=[{"name": "workflow-architect"}])
        if path.endswith("/prompt_async"):
            if prompts is not None:
                prompts.append(json.loads(request.content))
            return httpx.Response(204)
        if path.endswith("/abort"):
            return httpx.Response(200, json={})
        if path == "/event":
            body = "".join(f"data: {json.dumps(ev)}\n\n" for ev in events)
            return httpx.Response(
                200, content=body, headers={"content-type": "text/event-stream"}
            )
        if path == "/session/ses_test1/message":
            return httpx.Response(200, json=[{
                "info": {"role": "assistant", "id": "msg_1"},
                "parts": [{"type": "text", "text": final_text}],
            }])
        if path.endswith("/permissions/perm_1"):
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": path})

    return httpx.MockTransport(handler)


def _patched_engine(monkeypatch, events, final_text="", prompts=None):
    transport = _fake_server(events, final_text, prompts)
    eng = oce.OpenCodeEngine()
    eng._password = "pw"

    def client(auth, timeout=30.0):
        return httpx.AsyncClient(
            base_url="http://oc.test", transport=transport, auth=auth
        )

    monkeypatch.setattr(eng, "_client", client)
    return eng


def _ev(etype: str, **props) -> dict:
    return {"id": "evt_1", "type": etype, "properties": props}


def _collect(eng, text="hi", agent="general"):
    async def go():
        out = []
        active: dict = {}
        async for chunk in eng.stream_turn(
            text=text, conversation_id="c1", agent_id=agent,
            request_id="req1", active_clients=active,
        ):
            out.append(chunk)
        return out

    return asyncio.run(go())


def _payloads(chunks):
    out = []
    for c in chunks:
        if c.startswith("data:"):
            out.append(json.loads(c[5:]))
    return out


TEXT_PART = {
    "id": "prt_text", "sessionID": "ses_test1", "messageID": "msg_1",
    "type": "text", "text": "",
}
REASONING_PART = {
    "id": "prt_think", "sessionID": "ses_test1", "messageID": "msg_1",
    "type": "reasoning", "text": "",
}


def test_translates_text_deltas_and_suppresses_reasoning(monkeypatch):
    events = [
        _ev("message.part.updated", sessionID="ses_test1", part=REASONING_PART, time=1),
        _ev("message.part.delta", sessionID="ses_test1", messageID="msg_1",
            partID="prt_think", field="text", delta="thinking..."),
        _ev("message.part.updated", sessionID="ses_test1", part=TEXT_PART, time=2),
        _ev("message.part.delta", sessionID="ses_test1", messageID="msg_1",
            partID="prt_text", field="text", delta="Hello"),
        _ev("message.part.delta", sessionID="ses_test1", messageID="msg_1",
            partID="prt_text", field="text", delta=" world"),
        _ev("message.updated", sessionID="ses_test1",
            info={"id": "msg_1", "role": "assistant",
                  "tokens": {"input": 10, "output": 5}}),
        _ev("session.idle", sessionID="ses_test1"),
    ]
    eng = _patched_engine(monkeypatch, events)
    payloads = _payloads(_collect(eng))

    contents = [p["content"] for p in payloads if "content" in p]
    assert contents == ["Hello", " world"]  # reasoning delta suppressed
    meta = next(p["meta"] for p in payloads if "meta" in p)
    assert meta["input_tokens"] == 10
    assert meta["output_tokens"] == 5
    assert meta["engine"] == "opencode"
    assert eng.last_turn["text"] == "Hello world"


def test_translates_tool_call_and_result(monkeypatch):
    tool_running = {
        "id": "prt_tool", "sessionID": "ses_test1", "messageID": "msg_1",
        "type": "tool", "callID": "call_1", "tool": "bash",
        "state": {"status": "running", "input": {"command": "ls"}},
    }
    tool_done = {**tool_running, "state": {"status": "completed", "output": "file.txt"}}
    events = [
        _ev("message.part.updated", sessionID="ses_test1", part=tool_running, time=1),
        _ev("message.part.updated", sessionID="ses_test1", part=tool_running, time=2),
        _ev("message.part.updated", sessionID="ses_test1", part=tool_done, time=3),
        _ev("session.idle", sessionID="ses_test1"),
    ]
    eng = _patched_engine(monkeypatch, events, final_text="done")
    payloads = _payloads(_collect(eng))

    calls = [p["tool_call"] for p in payloads if "tool_call" in p]
    results = [p["tool_result"] for p in payloads if "tool_result" in p]
    assert calls == [{"id": "call_1", "name": "bash",
                      "arguments": {"command": "ls"}}]  # deduped
    assert results == [{"id": "call_1", "name": "bash", "result": "file.txt"}]
    assert eng.last_turn["tools"] == ["bash"]
    # No text streamed -> backfilled from the session's final message.
    contents = [p["content"] for p in payloads if "content" in p]
    assert contents == ["done"]


def test_session_error_surfaces(monkeypatch):
    events = [
        _ev("session.error", sessionID="ses_test1",
            error={"name": "ProviderAuthError",
                   "data": {"message": "no api key"}}),
    ]
    eng = _patched_engine(monkeypatch, events)
    payloads = _payloads(_collect(eng))
    assert any("no api key" in p.get("error", "") for p in payloads)


def test_other_sessions_events_ignored(monkeypatch):
    events = [
        _ev("message.part.updated", sessionID="ses_OTHER",
            part={**TEXT_PART, "id": "prt_x", "sessionID": "ses_OTHER"}, time=1),
        _ev("message.part.delta", sessionID="ses_OTHER", messageID="msg_x",
            partID="prt_x", field="text", delta="LEAK"),
        _ev("session.idle", sessionID="ses_test1"),
    ]
    eng = _patched_engine(monkeypatch, events, final_text="")
    payloads = _payloads(_collect(eng))
    assert not any("LEAK" in p.get("content", "") for p in payloads)


def test_forget_sessions_clears_map(monkeypatch, tmp_path):
    """A provider switch must drop conversation→session mappings, else opencode
    reuses a session bound to the old provider ('Model not found')."""
    from db import Database

    async def go():
        db = Database(str(tmp_path / "f.db"))
        await db.connect()
        await db.set_setting("opencode_sessions", {"conv-1": "ses_old"})
        await oce.engine.forget_sessions(db)
        stored = await db.get_setting("opencode_sessions", "missing")
        await db.close()
        return stored

    assert asyncio.run(go()) == {}


def test_forget_sessions_never_raises():
    """Best-effort: a DB failure must not block the config change."""

    class BadDB:
        async def set_setting(self, key, value):
            raise RuntimeError("db down")

    asyncio.run(oce.engine.forget_sessions(BadDB()))  # no exception


def test_session_mapping_reused(monkeypatch, tmp_path):
    from db import Database

    events = [_ev("session.idle", sessionID="ses_test1")]
    eng = _patched_engine(monkeypatch, events)

    async def go():
        db = Database(str(tmp_path / "e.db"))
        await db.connect()
        eng.set_db(db)
        auth = httpx.BasicAuth("opencode", "pw")
        first = await eng._session_for(auth, "conv-1")
        second = await eng._session_for(auth, "conv-1")
        stored = await db.get_setting("opencode_sessions", {})
        await db.close()
        return first, second, stored

    first, second, stored = asyncio.run(go())
    assert first == second == "ses_test1"
    assert stored == {"conv-1": "ses_test1"}


def test_prompt_names_the_active_model(monkeypatch, tmp_path):
    """Every prompt must carry the active provider/model explicitly, else a
    session created under an old provider replays its remembered model and
    fails with 'Model not found: <old>/<model>' even after opencode.json and
    llm_config are corrected (jupyter-mzink, 2026-07-24)."""
    from db import Database

    events = [_ev("session.idle", sessionID="ses_test1")]
    prompts: list[dict] = []
    eng = _patched_engine(monkeypatch, events, prompts=prompts)

    async def go():
        db = Database(str(tmp_path / "m.db"))
        await db.connect()
        # Stored model may carry a provider prefix — must be stripped.
        await db.set_llm_config(provider="gateway", model="gateway/default")
        eng.set_db(db)
        async for _ in eng.stream_turn(
            text="hi", conversation_id="c1", agent_id="general",
            request_id="req1", active_clients={},
        ):
            pass
        await db.close()

    asyncio.run(go())
    assert prompts[0]["model"] == {"providerID": "gateway", "modelID": "default"}


def test_prompt_defaults_to_gateway_without_config(monkeypatch, tmp_path):
    """No llm_config row -> the baked-in gateway default, matching the UI."""
    from db import Database

    events = [_ev("session.idle", sessionID="ses_test1")]
    prompts: list[dict] = []
    eng = _patched_engine(monkeypatch, events, prompts=prompts)

    async def go():
        db = Database(str(tmp_path / "n.db"))
        await db.connect()
        eng.set_db(db)
        async for _ in eng.stream_turn(
            text="hi", conversation_id="c1", agent_id="general",
            request_id="req1", active_clients={},
        ):
            pass
        await db.close()

    asyncio.run(go())
    assert prompts[0]["model"] == {"providerID": "gateway", "modelID": "default"}


def test_prompt_omits_model_without_db(monkeypatch):
    """No DB wired (defensive) -> omit the field, opencode picks its default."""
    events = [_ev("session.idle", sessionID="ses_test1")]
    prompts: list[dict] = []
    eng = _patched_engine(monkeypatch, events, prompts=prompts)
    _collect(eng)
    assert "model" not in prompts[0]


# --- engine selection endpoints ---


def test_engine_default_follows_env(client, monkeypatch):
    async def fake_status():
        return {"installed": False, "running": False,
                "url": "http://127.0.0.1:4747", "attach_command": "x"}

    monkeypatch.setattr(oce.engine, "status", fake_status)
    # conftest pins the test default to builtin for hermeticity.
    body = client.get("/api/chat/engine").json()
    assert body["engine"] == "builtin"
    assert body["opencode"]["installed"] is False

    # Production default (no env override) is opencode.
    monkeypatch.delenv("STUDIO_CHAT_ENGINE_DEFAULT", raising=False)
    assert client.get("/api/chat/engine").json()["engine"] == "opencode"

    # Garbage env value falls back to builtin, never an invalid engine.
    monkeypatch.setenv("STUDIO_CHAT_ENGINE_DEFAULT", "hal9000")
    assert client.get("/api/chat/engine").json()["engine"] == "builtin"


def test_engine_switch_to_opencode(client, monkeypatch):
    async def ok():
        return None

    async def fake_status():
        return {"installed": True, "running": True,
                "url": "http://127.0.0.1:4747", "attach_command": "x"}

    monkeypatch.setattr(oce.engine, "ensure_running", ok)
    monkeypatch.setattr(oce.engine, "status", fake_status)

    res = client.put("/api/chat/engine", json={"engine": "opencode"})
    assert res.status_code == 200
    assert res.json()["engine"] == "opencode"
    assert client.get("/api/chat/engine").json()["engine"] == "opencode"

    # Switch back.
    res = client.put("/api/chat/engine", json={"engine": "builtin"})
    assert res.json()["engine"] == "builtin"


def test_engine_switch_fails_closed(client, monkeypatch):
    async def broken():
        return "OpenCode is not installed"

    monkeypatch.setattr(oce.engine, "ensure_running", broken)
    res = client.put("/api/chat/engine", json={"engine": "opencode"})
    assert res.status_code == 200
    body = res.json()
    assert body["engine"] == "builtin"  # unchanged
    assert "not installed" in body["error"]


def test_engine_rejects_unknown(client):
    assert client.put(
        "/api/chat/engine", json={"engine": "hal9000"}
    ).status_code == 400


def test_chat_stream_dispatches_to_opencode(client, monkeypatch):
    """With engine=opencode set, /stream proxies and persists both turns."""

    async def ok():
        return None

    async def fake_turn(**kw):
        oce.engine.last_turn = {"text": "Hi from opencode", "tools": []}
        yield 'data: {"content": "Hi from opencode"}\n\n'

    monkeypatch.setattr(oce.engine, "ensure_running", ok)
    monkeypatch.setattr(oce.engine, "stream_turn", fake_turn)

    client.put("/api/chat/engine", json={"engine": "opencode"})
    res = client.post("/api/chat/stream", json={
        "messages": [{"role": "user", "content": "hello"}],
        "conversation_id": "conv-oc",
    })
    assert res.status_code == 200
    assert "Hi from opencode" in res.text
    assert "[DONE]" in res.text

    history = client.get(
        "/api/chat/history?conversation_id=conv-oc"
    ).json()["messages"]
    roles = [(m["role"], m["content"]) for m in history]
    assert ("user", "hello") in roles
    assert ("assistant", "Hi from opencode") in roles
