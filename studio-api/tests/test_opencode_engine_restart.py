"""The chat engine must be restarted when — and only when — its config moved.

OpenCode reads `opencode.json` once at startup and no endpoint reloads it, so a
provider change reaches the engine only through a restart; that is what the
previous adapter surfaced to users as "Model not found". The other half matters
just as much: propagation also runs on every boot and on every settings save, so
a restart on an unchanged config would drop a chat panel that had just
reattached, and killing the engine ends every turn in flight.

The restart is also deliberately never awaited. `supervisorctl restart` blocks
for the engine's stop and start, and awaiting it here held up studio-api's
startup and made a provider save hang for as long as the engine took.
"""

from __future__ import annotations

import asyncio
from typing import Any

import llm.propagator as propagator


class _FakeAdapter:
    """Stands in for OpenCodeAdapter, reporting whether the file changed."""

    def __init__(self, changed: bool):
        self.config_changed = changed
        self.calls = 0

    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        pass

    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        self.calls += 1

    def uninstall(self, workspace: str) -> None:
        pass


def _wire(monkeypatch, adapter, tools, restart_result=True):
    """Point the propagator at a fake adapter, tool list and restart."""
    restarts: list[str] = []

    async def fake_restart():
        restarts.append("restart")
        return restart_result

    from services import opencode_server

    monkeypatch.setattr(opencode_server, "restart", fake_restart)
    monkeypatch.setattr(
        propagator, "get_adapter", lambda tool_id: adapter if tool_id == "opencode" else None
    )
    monkeypatch.setattr(propagator, "prune_legacy_workspace_copies", lambda root: None)

    class FakeDB:
        async def get_llm_config(self):
            return {"provider": "gateway", "model": "m", "api_key": "k"}

        async def list_provider_configs(self):
            return []

        async def list_tools(self):
            return tools

    return restarts, FakeDB()


async def _propagate(db):
    await propagator.LLMPropagator().propagate(db)
    # The restart is scheduled, not awaited — give the loop a turn to run it.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def test_restart_fires_when_the_generated_config_changed(monkeypatch):
    adapter = _FakeAdapter(changed=True)
    restarts, db = _wire(monkeypatch, adapter, [{"tool_id": "opencode"}])

    asyncio.run(_propagate(db))

    assert adapter.calls == 1
    assert restarts == ["restart"]


def test_restart_is_skipped_when_the_config_is_unchanged(monkeypatch):
    adapter = _FakeAdapter(changed=False)
    restarts, db = _wire(monkeypatch, adapter, [{"tool_id": "opencode"}])

    asyncio.run(_propagate(db))

    assert adapter.calls == 1
    assert restarts == []


def test_engine_config_is_written_even_with_no_opencode_tool_row(monkeypatch):
    """`opencode serve` runs unconditionally, so its config is not optional.

    Uninstalling the OpenCode *tool* used to mean the engine's config was never
    written again — it kept running on what it had loaded, and the next restart
    would have left it with no provider at all.
    """
    adapter = _FakeAdapter(changed=True)
    restarts, db = _wire(monkeypatch, adapter, [{"tool_id": "claude-code"}])

    asyncio.run(_propagate(db))

    assert adapter.calls == 1
    assert restarts == ["restart"]


def test_a_failing_restart_does_not_fail_the_propagation(monkeypatch):
    """A settings save must succeed even if the engine cannot be restarted."""
    adapter = _FakeAdapter(changed=True)

    async def boom():
        raise RuntimeError("supervisorctl exploded")

    from services import opencode_server

    restarts, db = _wire(monkeypatch, adapter, [{"tool_id": "opencode"}])
    monkeypatch.setattr(opencode_server, "restart", boom)

    # Must not raise, and must not leave the propagation half-done.
    asyncio.run(_propagate(db))
    assert adapter.calls == 1


def test_restart_is_not_awaited_by_propagate(monkeypatch):
    """propagate() must return before a slow restart completes.

    Awaiting it put a `supervisorctl restart` — the engine's whole stop and
    start — inside studio-api's startup and inside the settings-save request.
    """
    adapter = _FakeAdapter(changed=True)
    restarts, db = _wire(monkeypatch, adapter, [{"tool_id": "opencode"}])
    finished: list[str] = []

    async def slow_restart():
        await asyncio.sleep(0.2)
        finished.append("done")
        return True

    from services import opencode_server

    monkeypatch.setattr(opencode_server, "restart", slow_restart)

    async def scenario():
        await propagator.LLMPropagator().propagate(db)
        # Returned already, with the restart still in flight.
        assert finished == []
        await asyncio.sleep(0.3)
        assert finished == ["done"]

    asyncio.run(scenario())
