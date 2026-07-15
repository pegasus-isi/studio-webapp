"""AntigravityAdapter — knowledge adapter for Antigravity.

Writes .antigravity/context.md and settings.json into the workspace.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any

import structlog

from knowledge.adapters import KnowledgeAdapter
from knowledge.common import (
    build_knowledge_appendix,
    home_path,
    remove_managed_block,
    unmerge_json_top,
    update_json,
    upsert_managed_block,
)
from llm.providers import PROVIDERS, provider_type

log = structlog.get_logger()

KNOWLEDGE_ROOT = os.environ.get(
    "KNOWLEDGE_ROOT", "/opt/pegasus-ai/knowledge"
)


class AntigravityAdapter(KnowledgeAdapter):
    """Translates canonical knowledge into Antigravity's native format."""

    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        dst_dir = os.path.join(workspace, ".antigravity")
        os.makedirs(dst_dir, exist_ok=True)

        # Copy context.md and append skills/agents appendix
        src = os.path.join(KNOWLEDGE_ROOT, "references", "PEGASUS_AI.md")
        dst = os.path.join(dst_dir, "context.md")
        if os.path.isfile(src):
            with open(src) as f:
                content = f.read()
            appendix = build_knowledge_appendix()
            body = content + ("\n" + appendix if appendix else "")
            with open(dst, "w") as f:
                f.write(body)
            # Global copy (~/.antigravity) so it's picked up regardless of the
            # cwd a terminal starts in. Managed block preserves any user content.
            upsert_managed_block(home_path(".antigravity", "context.md"), body)
            log.info("antigravity_context_written", path=dst)

        self.update_llm_config(workspace, llm_config)

    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Set env vars and write settings.json for the configured provider."""
        provider = llm_config.get("provider", "anthropic")
        ptype = provider_type(provider)  # custom-<id> -> "custom", etc.
        api_key = llm_config.get("api_key", "")
        model = llm_config.get("model", "")
        base_url = llm_config.get("base_url", "")

        preset = PROVIDERS.get(ptype, PROVIDERS.get("anthropic", {}))
        if not model:
            model = preset.get("default_model", "claude-sonnet-4-5-20250929")

        # Set the appropriate env var for the provider
        if api_key:
            env_var = preset.get("api_key_env")
            if env_var:
                os.environ[env_var] = api_key

            # Antigravity CLI is powered by Gemini and reads GOOGLE_API_KEY
            if ptype in ("google", "gemini"):
                os.environ["GOOGLE_API_KEY"] = api_key

        settings = {
            "provider": provider,
            "model": model,
        }
        if base_url:
            settings["base_url"] = base_url
        elif preset.get("base_url"):
            settings["base_url"] = preset["base_url"]

        if preset.get("api_key_env"):
            settings["api_key_env"] = preset["api_key_env"]

        # Workspace copy (project scope — Studio owns ~/work).
        dst_dir = os.path.join(workspace, ".antigravity")
        os.makedirs(dst_dir, exist_ok=True)
        settings_path = os.path.join(dst_dir, "settings.json")
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)

        # Global copy (~/.antigravity) — read-merge so it applies from any cwd
        # without discarding any user-owned settings.
        update_json(home_path(".antigravity", "settings.json"), settings)
        log.info(
            "antigravity_settings_written",
            path=settings_path,
            provider=provider,
            model=model,
        )

    def uninstall(self, workspace: str) -> None:
        path = os.path.join(workspace, ".antigravity")
        if os.path.isdir(path):
            shutil.rmtree(path)

        # Global cleanup — remove only what we added.
        remove_managed_block(home_path(".antigravity", "context.md"))
        unmerge_json_top(
            home_path(".antigravity", "settings.json"),
            ["provider", "model", "base_url", "api_key_env"],
        )
        log.info("antigravity_uninstalled", workspace=workspace)
