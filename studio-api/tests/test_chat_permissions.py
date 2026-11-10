"""Tests for the command-approval setting (SEC-4)."""

from __future__ import annotations

import asyncio
import os

import pytest

from services import chat_permissions as cp


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


def _collect(name, args, *, require_approval=True):
    from routers import chat

    async def go():
        return [ev async for ev in chat._run_tool_gated(
            name, {**args, "_call_id": "c"}, request_id="r",
            require_approval=require_approval,
        )]

    return asyncio.run(go())
