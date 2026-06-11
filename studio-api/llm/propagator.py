"""LLM configuration propagator — updates all installed AI tools."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import structlog

from knowledge.adapters import get_adapter

if TYPE_CHECKING:
    from db import Database

log = structlog.get_logger()

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)


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
                adapter.update_llm_config(WORKSPACE_ROOT, config)
                log.info("llm_propagated", tool_id=tool_id)
            except Exception as e:
                log.warning(
                    "llm_propagate_failed", tool_id=tool_id, error=str(e)
                )
