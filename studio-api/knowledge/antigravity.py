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
from knowledge.common import build_knowledge_appendix
from llm.providers import PROVIDERS

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
            with open(dst, "w") as f:
                f.write(content)
                if appendix:
                    f.write("\n" + appendix)
            log.info("antigravity_context_written", path=dst)

        self.update_llm_config(workspace, llm_config)

    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Set env vars and write settings.json for the configured provider."""
        provider = llm_config.get("provider", "anthropic")
        api_key = llm_config.get("api_key", "")
        model = llm_config.get("model", "")
        base_url = llm_config.get("base_url", "")

        preset = PROVIDERS.get(provider, PROVIDERS.get("anthropic", {}))
        if not model:
            model = preset.get("default_model", "claude-sonnet-4-5-20250929")

        # Set the appropriate env var for the provider
        if api_key:
            env_var = preset.get("api_key_env")
            if env_var:
                os.environ[env_var] = api_key

            # Antigravity CLI is powered by Gemini and reads GOOGLE_API_KEY
            if provider in ("google", "gemini"):
                os.environ["GOOGLE_API_KEY"] = api_key

        # Write settings.json with provider config
        dst_dir = os.path.join(workspace, ".antigravity")
        os.makedirs(dst_dir, exist_ok=True)

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

        settings_path = os.path.join(dst_dir, "settings.json")
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)
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
            log.info("antigravity_uninstalled", workspace=workspace)
