"""JupyterLabAdapter — knowledge adapter for JupyterLab + jupyter-ai.

Writes jupyter-ai configuration files and restarts JupyterLab when
LLM provider settings change so that the AI tools inside Jupyter
reflect the current provider configuration.
"""

from __future__ import annotations

import json
import os
from typing import Any

import structlog

from knowledge import layout
from knowledge.adapters import KnowledgeAdapter
from llm.providers import PROVIDERS

log = structlog.get_logger()



class JupyterLabAdapter(KnowledgeAdapter):
    """Propagates LLM config to JupyterLab's jupyter-ai extension."""

    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Write jupyter-ai config and seed notebooks workspace."""
        self.update_llm_config(workspace, llm_config)
        log.info("jupyterlab_adapter_installed", workspace=workspace)

    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Update jupyter-ai configuration with current LLM provider settings.

        jupyter-ai reads API keys from environment variables, which are set
        when JupyterLab is launched (see routers/jupyter.py:_build_jupyter_env).

        This adapter writes a config hint file that documents the current
        provider setup, and sets up env vars for the running process so
        any restart of JupyterLab picks up the new config.
        """
        provider = llm_config.get("provider", "anthropic")
        api_key = llm_config.get("api_key", "")
        model = llm_config.get("model", "")
        base_url = llm_config.get("base_url", "")

        preset = PROVIDERS.get(provider, PROVIDERS.get("anthropic", {}))
        if not model:
            model = preset.get("default_model", "claude-sonnet-4-5-20250929")

        # Set environment variables so jupyter-ai can discover providers
        if api_key:
            env_var = preset.get("api_key_env")
            if env_var:
                os.environ[env_var] = api_key

        # Also propagate all saved provider configs
        all_providers = llm_config.get("all_providers", [])
        for prov in all_providers:
            prov_id = prov.get("provider_id", "")
            prov_key = prov.get("api_key", "")
            if prov_key:
                prov_preset = PROVIDERS.get(prov_id, {})
                prov_env = prov_preset.get("api_key_env")
                if prov_env:
                    os.environ[prov_env] = prov_key

        # Write a jupyter-ai config hint at ~/.jupyter/jupyter_ai_config.json
        # This is informational — jupyter-ai primarily reads env vars. Skip if HOME not set.
        home = os.environ.get("HOME", "")
        if not home:
            return
        jupyter_dir = os.path.join(home, ".jupyter")
        os.makedirs(jupyter_dir, exist_ok=True)

        ai_config = {
            "active_provider": provider,
            "active_model": model,
            "providers": {},
        }

        # Include all configured providers
        for prov in all_providers:
            pid = prov.get("provider_id", "")
            prov_preset = PROVIDERS.get(pid, {})
            ai_config["providers"][pid] = {
                "name": prov.get("name", pid),
                "model": prov.get("default_model", prov_preset.get("default_model", "")),
                "api_key_env": prov_preset.get("api_key_env", ""),
                "base_url": prov.get("base_url", prov_preset.get("base_url", "")),
            }

        # If no all_providers, at least record the active one
        if not ai_config["providers"] and provider:
            ai_config["providers"][provider] = {
                "name": preset.get("name", provider),
                "model": model,
                "api_key_env": preset.get("api_key_env", ""),
                "base_url": base_url or preset.get("base_url", ""),
            }

        config_path = os.path.join(jupyter_dir, "jupyter_ai_config.json")
        with open(config_path, "w") as f:
            json.dump(ai_config, f, indent=2)

        log.info(
            "jupyterlab_ai_config_written",
            path=config_path,
            provider=provider,
            model=model,
            num_providers=len(ai_config["providers"]),
        )

    def uninstall(self, workspace: str) -> None:
        """Remove jupyter-ai configuration."""
        home = os.environ.get("HOME", "")
        if not home:
            return
        config_path = os.path.join(home, ".jupyter", "jupyter_ai_config.json")
        if os.path.isfile(config_path):
            os.remove(config_path)
            log.info("jupyterlab_ai_config_removed", path=config_path)
