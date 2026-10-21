"""Tests for the per-turn command-approval gate (SEC-4)."""

from __future__ import annotations

import asyncio
import os

import pytest

from services import chat_permissions as cp


def test_resolve_matches_pending_future():
    async def go():
        task = asyncio.create_task(cp.await_decision("req1", "call1"))
        await asyncio.sleep(0.01)  # let the future register
        assert cp.resolve("req1", "call1", "approve") is True
        return await task

    assert asyncio.run(go()) == "approve"


def test_resolve_unknown_is_noop():
    assert cp.resolve("nope", "nope", "approve") is False


def test_deny_all_for_releases_waiters():
    async def go():
        t1 = asyncio.create_task(cp.await_decision("reqX", "a"))
        t2 = asyncio.create_task(cp.await_decision("reqX", "b"))
        await asyncio.sleep(0.01)
        n = cp.deny_all_for("reqX")
        return n, await t1, await t2

    n, d1, d2 = asyncio.run(go())
    assert n == 2
    assert d1 == "deny" and d2 == "deny"


def test_timeout_returns_timeout(monkeypatch):
    monkeypatch.setattr(cp, "_DECISION_TIMEOUT_S", 0.05)

    async def go():
        return await cp.await_decision("reqT", "c")

    assert asyncio.run(go()) == "timeout"


def test_require_approval_defaults_true():
    class DB:
        async def get_setting(self, key, default=None):
            return default

    assert asyncio.run(cp.require_approval(DB())) is True


def test_require_approval_respects_off():
    class DB:
        async def get_setting(self, key, default=None):
            return "0"

    assert asyncio.run(cp.require_approval(DB())) is False


def test_require_approval_fails_safe_on_db_error():
    class DB:
        async def get_setting(self, key, default=None):
            raise RuntimeError("db down")

    # A DB failure must NOT silently revert to auto-run.
    assert asyncio.run(cp.require_approval(DB())) is True


# --- endpoints ---


def test_command_approval_roundtrip(client):
    assert client.get("/api/chat/command-approval").json()["require_approval"] is True
    r = client.put(
        "/api/chat/command-approval", json={"require_approval": False}
    )
    assert r.json()["require_approval"] is False
    assert client.get("/api/chat/command-approval").json()["require_approval"] is False


def test_permission_endpoint_rejects_bad_decision(client):
    r = client.post(
        "/api/chat/permission",
        json={"request_id": "r", "call_id": "c", "decision": "maybe"},
    )
    assert r.status_code == 400


def test_permission_endpoint_noop_when_nothing_waiting(client):
    r = client.post(
        "/api/chat/permission",
        json={"request_id": "r", "call_id": "c", "decision": "approve"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "noop"


# --- builtin gated tool flow ---


async def _drive(agen, request_id, call_id, decision, *, delay=0.02):
    """Consume a _run_tool_gated generator while resolving its prompt."""
    collected = []

    async def consume():
        async for ev in agen:
            collected.append(ev)

    task = asyncio.create_task(consume())
    await asyncio.sleep(delay)  # let it emit permission_request + block
    cp.resolve(request_id, call_id, decision)
    await task
    return collected


def test_gated_denial_skips_execution(tmp_path, monkeypatch):
    from routers import chat

    ran = []

    async def fake_execute(name, args):
        ran.append((name, args))
        return "SHOULD NOT RUN"

    monkeypatch.setattr(chat, "_execute_tool", fake_execute)

    async def go():
        agen = chat._run_tool_gated(
            "run_command",
            {"command": "rm -rf /", "_call_id": "cid1"},
            request_id="rid1",
            require_approval=True,
        )
        return await _drive(agen, "rid1", "cid1", "deny")

    events = asyncio.run(go())
    kinds = [next(iter(e)) for e in events]
    assert "permission_request" in kinds
    assert ran == []  # denied -> never executed
    final = next(e["_final_result"] for e in events if "_final_result" in e)
    assert "denied" in final.lower()


def test_gated_approval_executes():
    import os

    from routers import chat

    os.makedirs(chat.WORKSPACE_ROOT, exist_ok=True)

    async def go():
        agen = chat._run_tool_gated(
            "run_command",
            {"command": "echo approved-ok", "_call_id": "cid2"},
            request_id="rid2",
            require_approval=True,
        )
        return await _drive(agen, "rid2", "cid2", "approve")

    events = asyncio.run(go())
    final = next(e["_final_result"] for e in events if "_final_result" in e)
    assert "approved-ok" in final


def test_ungated_tool_runs_without_prompt():
    """With approval off, run_command executes and emits no permission card."""
    import os

    from routers import chat

    os.makedirs(chat.WORKSPACE_ROOT, exist_ok=True)

    async def go():
        events = []
        async for ev in chat._run_tool_gated(
            "run_command",
            {"command": "echo nogate", "_call_id": "cid3"},
            request_id="rid3",
            require_approval=False,
        ):
            events.append(ev)
        return events

    events = asyncio.run(go())
    kinds = [next(iter(e)) for e in events]
    assert "permission_request" not in kinds
    final = next(e["_final_result"] for e in events if "_final_result" in e)
    assert "nogate" in final


def test_reading_tools_are_never_gated():
    """Listing and reading stay auto-approved even with approval on."""
    from routers import chat

    async def go():
        events = []
        async for ev in chat._run_tool_gated(
            "list_directory",
            {"path": "", "_call_id": "cid4"},
            request_id="rid4",
            require_approval=True,
        ):
            events.append(ev)
        return events

    events = asyncio.run(go())
    kinds = [next(iter(e)) for e in events]
    assert "permission_request" not in kinds


def _collect(name, args, *, require_approval=True):
    from routers import chat

    async def go():
        return [ev async for ev in chat._run_tool_gated(
            name, {**args, "_call_id": "c"}, request_id="r",
            require_approval=require_approval,
        )]

    return asyncio.run(go())


def test_writes_where_the_assistant_works_are_not_gated(tmp_path, monkeypatch):
    from routers import chat

    monkeypatch.setattr(chat, "WORKSPACE_ROOT", str(tmp_path))
    for rel in ("workflows/r/workflow_generator.py", "data/in.csv"):
        events = _collect("write_file", {"path": rel, "content": "x"})
        assert not any("permission_request" in e for e in events), rel
        assert (tmp_path / rel).read_text() == "x"


def test_writes_to_files_that_run_later_are_gated(tmp_path, monkeypatch):
    """~/.bashrc, ~/opencode.json, PATH dirs, a repo's git hooks: each is a
    deferred shell command, so each asks like one, and a denial writes nothing."""
    from routers import chat

    monkeypatch.setattr(chat, "WORKSPACE_ROOT", str(tmp_path))
    for rel in (".bashrc", "opencode.json", ".npm-global/bin/ls",
                "workflows/r/.git/hooks/pre-commit", "notes.txt"):
        assert chat._write_needs_approval("write_file", {"path": rel}), rel

    async def go():
        agen = chat._run_tool_gated(
            "write_file", {"path": ".bashrc", "content": "evil", "_call_id": "w1"},
            request_id="rw1", require_approval=True,
        )
        return await _drive(agen, "rw1", "w1", "deny")

    events = asyncio.run(go())
    req = next(e["permission_request"] for e in events if "permission_request" in e)
    assert req["name"] == "write_file" and ".bashrc" in req["summary"]
    assert not (tmp_path / ".bashrc").exists()
    final = next(e["_final_result"] for e in events if "_final_result" in e)
    assert final.startswith("File not written")


def test_a_symlink_out_of_workflows_is_gated(tmp_path, monkeypatch):
    from routers import chat

    monkeypatch.setattr(chat, "WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "workflows" / "r").mkdir(parents=True)
    (tmp_path / ".bashrc").write_text("")
    os.symlink(tmp_path / ".bashrc", tmp_path / "workflows" / "r" / "rc")
    assert chat._write_needs_approval("write_file", {"path": "workflows/r/rc"})


def test_writes_are_not_gated_when_approval_is_off(tmp_path, monkeypatch):
    from routers import chat

    monkeypatch.setattr(chat, "WORKSPACE_ROOT", str(tmp_path))
    events = _collect("write_file", {"path": ".bashrc", "content": "x"}, require_approval=False)
    assert not any("permission_request" in e for e in events)
