"""LLM configuration propagator — updates all installed AI tools."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from knowledge.adapters import get_adapter
from knowledge.common import config_root, prune_legacy_workspace_copies
from llm.providers import PROVIDER_DEFAULTS, fetch_models, provider_type
from workspace import legacy_workspace_root, workspace_root

if TYPE_CHECKING:
    from db import Database

log = structlog.get_logger()

WORKSPACE_ROOT = workspace_root()

# Bound on each provider's /models probe. Propagation runs at startup and on
# every provider save, so a hung endpoint must delay it by at most this long
# (probes run concurrently), never block it.
MODEL_FETCH_TIMEOUT = 6.0


async def _attach_model_lists(providers: list[dict[str, Any]]) -> None:
    """Fetch each provider's live /models list and attach it as ``models``.

    opencode cannot discover models for OpenAI-compatible providers — its
    picker lists exactly what the generated config declares — so declaring
    only default_model left at most one selectable model per endpoint (#207).
    Any failure (endpoint down, auth, timeout) simply leaves ``models`` unset
    and the adapters fall back to the stored default_model.
    """

    async def one(prov: dict[str, Any]) -> None:
        ptype = provider_type(prov.get("provider_id") or "")
        if ptype == "anthropic":
            return  # no OpenAI-style /models; opencode's SDK knows its models
        base_url = (
            prov.get("base_url")
            or PROVIDER_DEFAULTS.get(ptype, {}).get("base_url", "")
        )
        if not base_url:
            return
        try:
            models, err = await asyncio.wait_for(
                fetch_models(base_url, prov.get("api_key") or ""),
                timeout=MODEL_FETCH_TIMEOUT,
            )
        except Exception as e:
            models, err = [], str(e)
        if models:
            prov["models"] = models
        else:
            log.info(
                "llm_model_list_unavailable",
                provider_id=prov.get("provider_id"),
                error=err,
            )

    await asyncio.gather(*(one(p) for p in providers))


# Restart tasks in flight, kept referenced so the event loop cannot garbage
# collect a task nobody is awaiting.
_restart_tasks: set[asyncio.Task[Any]] = set()


def _schedule_engine_restart(adapter: Any) -> None:
    """Restart the chat engine, in the background, if its config changed.

    OpenCode reads opencode.json once at startup and no endpoint reloads it
    (docs/OPENCODE-CHAT-SPIKE.md), so a provider change reaches the engine only
    through a restart — which is what the previous adapter surfaced as "Model
    not found".

    Only when the file actually changed: propagation also runs at every startup,
    and restarting on every boot would drop a chat panel that had just
    reattached.

    Never awaited. `supervisorctl restart` blocks for the stop *and* the start
    of the process, so awaiting it here would hold up studio-api's own startup
    — the thing the entrypoint goes out of its way to keep unblocked — and
    would make a provider save in Settings hang for as long as the engine takes
    to come back.
    """
    if not getattr(adapter, "config_changed", False):
        return
    from services import opencode_server

    try:
        task = asyncio.ensure_future(opencode_server.restart())
    except RuntimeError as e:  # no running loop (a CLI propagation)
        log.info("opencode_restart_not_scheduled", error=str(e))
        return
    _restart_tasks.add(task)
    task.add_done_callback(_restart_tasks.discard)


class LLMPropagator:
    """Propagates LLM config to all installed AI tools via their adapters."""

    async def propagate(self, db: Database) -> None:
        """Read current LLM config and push it to each installed tool's adapter.

        Includes all saved provider configs so adapters like Codex CLI can
        write multi-provider config files.
        """
        config = await db.get_llm_config()
        if not config:
            log.info("llm_propagate_skip", reason="no config set")
            return

        # Attach all saved provider configs for adapters that need them
        try:
            all_providers = await db.list_provider_configs()
            if all_providers:
                await _attach_model_lists(all_providers)
                config["all_providers"] = all_providers
        except Exception:
            pass  # Table may not exist yet on first run

        # The chat panel's engine reads its permission rules out of the same
        # generated config (#367), so the setting that used to be enforced in
        # Python has to travel with the provider config.
        try:
            from services.chat_permissions import require_approval

            config["require_command_approval"] = await require_approval(db)
        except Exception:
            pass  # default posture (prompt) is applied by the adapter

        # Telemetry consent travels the same way (#367 step 6): the engine's
        # OTel plugin options are part of the generated config, so whether
        # traces may carry prompt content is decided here, once, and written
        # with everything else. Absent on failure, which the adapter reads as
        # "no consent" — the safe direction.
        try:
            from services import telemetry

            config["telemetry_content_capture"] = await telemetry.content_capture(db)
        except Exception:
            pass

        installed = await db.list_tools()
        opencode_written = False

        for tool in installed:
            tool_id = tool["tool_id"]
            adapter = get_adapter(tool_id)
            if adapter is None:
                continue
            try:
                prune_legacy_workspace_copies(legacy_workspace_root(WORKSPACE_ROOT))
                adapter.update_llm_config(config_root(), config)
                log.info("llm_propagated", tool_id=tool_id)
            except Exception as e:
                log.warning(
                    "llm_propagate_failed", tool_id=tool_id, error=str(e)
                )
                continue

            if tool_id == "opencode":
                opencode_written = True
                _schedule_engine_restart(adapter)

        if not opencode_written:
            # The chat engine's config is not optional the way a terminal
            # tool's is: `opencode serve` runs unconditionally, so if the
            # opencode *tool* row is absent — never seeded, or uninstalled
            # through Settings → Tools — the engine would keep running and
            # never be given a provider again, and the next restart would leave
            # it with no config at all.
            adapter = get_adapter("opencode")
            if adapter is not None:
                try:
                    adapter.update_llm_config(config_root(), config)
                    log.info("llm_propagated", tool_id="opencode", reason="engine")
                    _schedule_engine_restart(adapter)
                except Exception as e:
                    log.warning("llm_propagate_failed", tool_id="opencode", error=str(e))
