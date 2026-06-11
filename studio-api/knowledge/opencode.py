"""OpenCodeAdapter — knowledge adapter for OpenCode.

Ported from config_builder.py:build_opencode_config.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any

import structlog

from knowledge.adapters import KnowledgeAdapter
from knowledge.common import copy_agents_to_dir, copy_skills_to_dir
from llm.providers import PROVIDERS, _resolve_api_key, _resolve_base_url

log = structlog.get_logger()

KNOWLEDGE_ROOT = os.environ.get(
    "KNOWLEDGE_ROOT", "/opt/pegasus-ai/knowledge"
)


class OpenCodeAdapter(KnowledgeAdapter):
    """Translates canonical knowledge into OpenCode's native format."""

    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        # Copy PEGASUS_AI.md as AGENTS.md
        src = os.path.join(KNOWLEDGE_ROOT, "references", "PEGASUS_AI.md")
        dst = os.path.join(workspace, "AGENTS.md")
        if os.path.isfile(src):
            shutil.copy2(src, dst)

        # Seed .opencode/agents/ and .opencode/skills/
        copy_agents_to_dir(os.path.join(workspace, ".opencode", "agents"))
        copy_skills_to_dir(os.path.join(workspace, ".opencode", "skills"))

        # Write opencode.json
        self.update_llm_config(workspace, llm_config)
        log.info("opencode_installed", workspace=workspace)

    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Rewrite opencode.json with the current provider config."""
        provider = llm_config.get("provider", "anthropic")
        model = llm_config.get("model")
        api_key = llm_config.get("api_key", "")
        base_url = llm_config.get("base_url", "")

        preset = PROVIDERS.get(provider, PROVIDERS["anthropic"])
        if not model:
            model = preset.get("default_model", "claude-sonnet-4-5-20250929")

        # Build provider config
        options: dict[str, str] = {}
        if provider == "anthropic":
            if api_key:
                options["apiKey"] = api_key
        elif provider == "openai":
            if api_key:
                options["apiKey"] = api_key
        elif provider == "ollama":
            options["baseURL"] = base_url or "http://localhost:11434/v1"
            options["apiKey"] = "ollama"
        else:
            if base_url:
                options["baseURL"] = base_url
            if api_key:
                options["apiKey"] = api_key

        config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                provider: {
                    "npm": preset.get("npm", "@ai-sdk/openai-compatible"),
                    "name": preset.get("name", provider),
                    "options": options,
                    "models": {model: {"name": model}},
                },
            },
            "model": f"{provider}/{model}",
            "instructions": ["AGENTS.md"],
            "mcp": {
                "pegasus-docs": {
                    "type": "remote",
                    "url": "https://gitmcp.io/pegasus-isi/pegasus",
                    "enabled": True,
                },
                "kiso-docs": {
                    "type": "remote",
                    "url": "https://gitmcp.io/pegasus-isi/kiso",
                    "enabled": True,
                },
            },
        }

        config_path = os.path.join(workspace, "opencode.json")
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        log.info("opencode_config_written", path=config_path)

    def uninstall(self, workspace: str) -> None:
        for target in ("AGENTS.md", "opencode.json", ".opencode"):
            path = os.path.join(workspace, target)
            if os.path.isfile(path):
                os.remove(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
        log.info("opencode_uninstalled", workspace=workspace)
