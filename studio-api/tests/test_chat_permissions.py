"""Tests for the per-turn command-approval gate (SEC-4)."""

from __future__ import annotations

import asyncio

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


def _final_text(events, chat):
    out = next(e["_final_result"] for e in events if "_final_result" in e)
    return out.text if isinstance(out, chat.ToolOutcome) else out


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
    final = _final_text(events, chat)
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
    permission = next(
        e["permission_request"] for e in events if "permission_request" in e
    )
    final = _final_text(events, chat)
    assert permission["kind"] == "command"
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
    final = _final_text(events, chat)
    assert "nogate" in final


def test_file_tools_are_never_gated():
    """Only shell commands are gated; file tools stay auto-approved even with
    approval on (they are workspace-confined)."""
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


def test_denial_suppression_excludes_approval_tools():
    """A denied shell command must not remove the next approval prompt."""
    from routers import chat

    assert chat._DENIAL_SUPPRESSED_TOOLS.isdisjoint(
        chat._APPROVAL_REQUIRED_TOOLS
    )


def test_gated_tool_prompts_again_after_a_denial():
    """Denying one shell command does not disable later gated calls."""
    import os

    from routers import chat

    os.makedirs(chat.WORKSPACE_ROOT, exist_ok=True)

    async def go():
        first = chat._run_tool_gated(
            "run_command",
            {"command": "echo first", "_call_id": "cidA"},
            request_id="ridA",
            require_approval=True,
        )
        events1 = await _drive(first, "ridA", "cidA", "deny")

        second = chat._run_tool_gated(
            "run_command",
            {"command": "echo second", "_call_id": "cidB"},
            request_id="ridB",
            require_approval=True,
        )
        events2 = await _drive(second, "ridB", "cidB", "approve")
        return events1, events2

    events1, events2 = asyncio.run(go())
    kinds1 = [next(iter(e)) for e in events1]
    kinds2 = [next(iter(e)) for e in events2]

    assert "permission_request" in kinds1
    assert "permission_request" in kinds2


@pytest.mark.parametrize(
    "command",
    [
        "pegasus-plan --submit workflow.yml",
        "pegasus-plan -s compute -o local --submit wf.yml",
        "pegasus-run /runs/run0001",
        "python -c 'plan_submit()'",
        "wf.plan(submit=True)",
        "PEGASUS-RUN /runs/run0001",
    ],
)
def test_submit_commands_are_flagged(command):
    from routers import chat

    assert chat._requires_submit_approval(
        "run_command", {"command": command}
    )


@pytest.mark.parametrize(
    "command",
    [
        "pegasus-plan -s compute -o local wf.yml",
        "pegasus-status -l",
        "echo submit",
        "grep submit README.md",
        "pegasus-plan --no-submit wf.yml",
        "echo pegasus-runner",
    ],
)
def test_non_submit_commands_are_not_flagged(command):
    from routers import chat

    assert not chat._requires_submit_approval(
        "run_command", {"command": command}
    )


def test_non_shell_tools_are_not_flagged():
    from routers import chat

    assert not chat._requires_submit_approval(
        "write_file", {"path": "x", "content": "--submit"}
    )


def test_submit_command_gated_when_setting_off(monkeypatch):
    """Turning off routine confirmation must not allow an implicit submit."""
    from routers import chat

    ran = []

    async def fake_execute(name, args):
        ran.append((name, args))
        return "SHOULD NOT RUN"

    monkeypatch.setattr(chat, "_execute_tool", fake_execute)

    async def go():
        agen = chat._run_tool_gated(
            "run_command",
            {
                "command": "pegasus-plan --submit workflow.yml",
                "_call_id": "cidSubmit",
            },
            request_id="ridSubmit",
            require_approval=False,
        )
        return await _drive(agen, "ridSubmit", "cidSubmit", "deny")

    events = asyncio.run(go())
    kinds = [next(iter(e)) for e in events]
    permission = next(
        e["permission_request"] for e in events if "permission_request" in e
    )

    assert "permission_request" in kinds
    assert permission["kind"] == "submit"
    assert ran == []
    final = _final_text(events, chat)
    assert "denied" in final.lower()


def test_forced_submit_approval_executes_when_approved(monkeypatch):
    from routers import chat

    ran = []

    async def fake_execute(name, args, scope=None):
        ran.append((name, args))
        return "APPROVED"

    monkeypatch.setattr(chat, "_execute_tool", fake_execute)

    async def go():
        agen = chat._run_tool_gated(
            "run_command",
            {"command": "pegasus-run /runs/run1", "_call_id": "cidRun"},
            request_id="ridRun",
            require_approval=False,
        )
        return await _drive(agen, "ridRun", "cidRun", "approve")

    events = asyncio.run(go())
    permission = next(
        e["permission_request"] for e in events if "permission_request" in e
    )
    final = _final_text(events, chat)

    assert permission["kind"] == "submit"
    assert final == "APPROVED"
    assert ran


def test_validation_plan_still_ungated_when_setting_off(monkeypatch):
    from routers import chat

    ran = []

    async def fake_execute(name, args, scope=None):
        ran.append((name, args))
        return "VALIDATION"

    monkeypatch.setattr(chat, "_execute_tool", fake_execute)

    async def go():
        events = []
        async for ev in chat._run_tool_gated(
            "run_command",
            {
                "command": (
                    "pegasus-plan -s compute -o local workflow.yml"
                ),
                "_call_id": "cidValidate",
            },
            request_id="ridValidate",
            require_approval=False,
        ):
            events.append(ev)
        return events

    events = asyncio.run(go())
    kinds = [next(iter(e)) for e in events]
    final = _final_text(events, chat)

    assert "permission_request" not in kinds
    assert final == "VALIDATION"
    assert ran
