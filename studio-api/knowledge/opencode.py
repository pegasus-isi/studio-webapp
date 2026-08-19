"""OpenCodeAdapter — knowledge adapter for OpenCode.

Ported from config_builder.py:build_opencode_config.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any

import structlog

from knowledge import layout
from knowledge.adapters import KnowledgeAdapter
from knowledge.common import (
    build_knowledge_appendix,
    ensure_context_file,
    link_agents_to_dir,
    link_skills_to_dir,
)
from llm.providers import PROVIDERS, _resolve_api_key, _resolve_base_url, provider_type

log = structlog.get_logger()



def _global_config_dir() -> str:
    """OpenCode's global config dir (XDG). Config here applies from ANY cwd —
    the workspace-only config was picked up only when opencode started in
    ~/work, so terminals launched elsewhere (PVC mounts at ~) fell back to the
    default 'Zen' provider."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.environ.get("HOME", "/tmp"), ".config"
    )
    return os.path.join(base, "opencode")


class OpenCodeAdapter(KnowledgeAdapter):
    """Translates canonical knowledge into OpenCode's native format."""

    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        # Copy PEGASUS_AI.md as AGENTS.md — into the workspace and the global
        # config dir, so the config's `instructions: ["AGENTS.md"]` resolves
        # whether opencode starts in ~/work or anywhere else.
        content = layout.context_document()
        if content:
            appendix = build_knowledge_appendix()
            body = content + ("\n" + appendix if appendix else "")
            for dst_dir in (workspace, _global_config_dir()):
                try:
                    os.makedirs(dst_dir, exist_ok=True)
                    with open(os.path.join(dst_dir, "AGENTS.md"), "w") as f:
                        f.write(body)
                except OSError as e:
                    log.warning("opencode_agents_write_failed", dir=dst_dir, error=str(e))

        # Seed .opencode/agents/ and .opencode/skills/
        link_agents_to_dir(os.path.join(workspace, ".opencode", "agents"))
        link_skills_to_dir(os.path.join(workspace, ".opencode", "skills"))

        # Write opencode.json (global — read from any cwd)
        self.update_llm_config(workspace, llm_config)
        log.info("opencode_installed", workspace=workspace)

    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Rewrite opencode.json with the current provider config."""
        # Runs on every start, so it is also where a deleted AGENTS.md comes back
        # and where the skill/agent links are (re)made. install() alone was not
        # enough: startup only calls this, so when the config root moved to $HOME
        # the links were never created there and the assistant ran with no skills
        # at all — improvising instead of following pegasus-scaffold.
        ensure_context_file(os.path.join(workspace, "AGENTS.md"))
        ensure_context_file(os.path.join(_global_config_dir(), "AGENTS.md"))
        link_agents_to_dir(os.path.join(workspace, ".opencode", "agents"))
        link_skills_to_dir(os.path.join(workspace, ".opencode", "skills"))
        provider = llm_config.get("provider", "anthropic")
        ptype = provider_type(provider)  # custom-<id> -> "custom", etc.
        model = llm_config.get("model")
        api_key = llm_config.get("api_key", "")
        base_url = llm_config.get("base_url", "")

        preset = PROVIDERS.get(ptype, PROVIDERS["anthropic"])
        if not model:
            model = preset.get("default_model", "claude-sonnet-4-5-20250929")
        # The opencode `model` field is "<providerID>/<modelID>". Strip any
        # provider prefix that may already be on the stored model so we build a
        # clean id (e.g. "default"), not "custom-x/default" doubled up.
        model_id = str(model).rsplit("/", 1)[-1]

        # Build provider config
        options: dict[str, str] = {}
        if ptype == "anthropic":
            if api_key:
                options["apiKey"] = api_key
        elif ptype == "openai":
            if api_key:
                options["apiKey"] = api_key
        elif ptype == "ollama":
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
                    "models": {model_id: {"name": model_id}},
                },
            },
            "model": f"{provider}/{model_id}",
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

        # Global config only. It is read from any cwd, so the workspace copy
        # this used to also write was redundant — byte-identical content in a
        # second place to keep in sync, and the only non-hidden file Studio put
        # in the directory users are told to keep workflows in. uninstall()
        # still removes the legacy copy for workspaces that have one.
        blob = json.dumps(config, indent=2)
        global_dir = _global_config_dir()
        targets = [os.path.join(global_dir, "opencode.json")]
        for path in targets:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(blob)
                log.info("opencode_config_written", path=path)
            except OSError as e:
                log.warning("opencode_config_write_failed", path=path, error=str(e))

    def uninstall(self, workspace: str) -> None:
        paths = [
            os.path.join(workspace, t)
            for t in ("AGENTS.md", "opencode.json", ".opencode")
        ]
        # Also remove the global config we wrote.
        gdir = _global_config_dir()
        paths += [os.path.join(gdir, "opencode.json"), os.path.join(gdir, "AGENTS.md")]
        for path in paths:
            try:
                if os.path.isfile(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
            except OSError:
                pass
        log.info("opencode_uninstalled", workspace=workspace)
