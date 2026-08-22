"""LLM configuration propagator — updates all installed AI tools."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

import structlog

from knowledge.adapters import get_adapter
from knowledge.common import config_root, prune_legacy_workspace_copies
from llm.providers import PROVIDER_DEFAULTS, fetch_models, provider_type

if TYPE_CHECKING:
    from db import Database

log = structlog.get_logger()

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)

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

        installed = await db.list_tools()
        if not installed:
            log.info("llm_propagate_skip", reason="no tools installed")
            return

        for tool in installed:
            tool_id = tool["tool_id"]
            adapter = get_adapter(tool_id)
            if adapter is None:
                continue
            try:
                prune_legacy_workspace_copies(WORKSPACE_ROOT)
                adapter.update_llm_config(config_root(), config)
                log.info("llm_propagated", tool_id=tool_id)
            except Exception as e:
                log.warning(
                    "llm_propagate_failed", tool_id=tool_id, error=str(e)
                )
