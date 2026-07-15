"""ClaudeCodeAdapter — knowledge adapter for Claude Code.

Install: copy CLAUDE.md, write .mcp.json, register plugins via settings.json.
Writes settings.json for LLM provider config + marketplace/plugin registration.
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
    merge_json_mapping,
    remove_managed_block,
    unmerge_json_mapping,
    upsert_managed_block,
)
from llm.providers import PROVIDERS, provider_type

log = structlog.get_logger()

KNOWLEDGE_ROOT = os.environ.get(
    "KNOWLEDGE_ROOT", "/opt/pegasus-ai/knowledge"
)

# Marketplace repo for plugin registration
MARKETPLACE_REPO = "pegasus-isi/claude-plugin-marketplace"

# Plugins to enable by default
DEFAULT_PLUGINS = [
    "pegasus-ai@scitech",
    "pegasus-dev@scitech",
]


class ClaudeCodeAdapter(KnowledgeAdapter):
    """Translates canonical knowledge into Claude Code's native format."""

    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        # Copy CLAUDE.md from references and append skills/agents appendix.
        src = os.path.join(KNOWLEDGE_ROOT, "references", "PEGASUS_AI.md")
        if os.path.isfile(src):
            with open(src) as f:
                content = f.read()
            appendix = build_knowledge_appendix()
            body = content + ("\n" + appendix if appendix else "")
            # Workspace CLAUDE.md (project memory; Studio owns ~/work).
            dst = os.path.join(workspace, "CLAUDE.md")
            with open(dst, "w") as f:
                f.write(body)
            # Global ~/.claude/CLAUDE.md is loaded from ANY cwd. Use a managed
            # block so we never clobber a user's own global memory.
            upsert_managed_block(home_path(".claude", "CLAUDE.md"), body)
            log.info("claude_code_claude_md_written", path=dst)

        # Write .mcp.json from knowledge/mcp/ (project scope) and merge the same
        # servers into ~/.claude.json (user scope) so they resolve from any cwd.
        mcp_src = os.path.join(KNOWLEDGE_ROOT, "mcp", "servers.json")
        mcp_dst = os.path.join(workspace, ".mcp.json")
        if os.path.isfile(mcp_src):
            shutil.copy2(mcp_src, mcp_dst)
            try:
                with open(mcp_src) as f:
                    servers = (json.load(f) or {}).get("mcpServers", {})
                if servers:
                    merge_json_mapping(
                        home_path(".claude.json"), "mcpServers", servers
                    )
            except (json.JSONDecodeError, OSError) as e:
                log.warning("claude_code_global_mcp_failed", error=str(e))
            log.info("claude_code_mcp_json_written", path=mcp_dst)

        # Propagate LLM config + register plugins (writes settings.json)
        self.update_llm_config(workspace, llm_config)

    def _read_settings(self, settings_path: str) -> dict[str, Any]:
        """Read existing settings.json or return empty dict."""
        if os.path.isfile(settings_path):
            try:
                with open(settings_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _write_settings(
        self, settings_path: str, settings: dict[str, Any],
    ) -> None:
        """Write settings.json, creating parent dirs if needed."""
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)

    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Set env vars and write settings.json with provider + plugin config."""
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

            # Claude Code always reads ANTHROPIC_API_KEY
            if ptype == "anthropic":
                os.environ["ANTHROPIC_API_KEY"] = api_key

        # Write settings.json to BOTH the workspace (project scope) and
        # ~/.claude (user scope). The user-scope copy makes `claude` pick up the
        # provider + plugins from any cwd, not only when launched inside ~/work.
        # Each target is read-merged so we preserve the user's existing settings.
        targets = [
            os.path.join(workspace, ".claude", "settings.json"),
            home_path(".claude", "settings.json"),
        ]
        for settings_path in targets:
            settings = self._read_settings(settings_path)

            # LLM provider config
            settings["provider"] = provider
            settings["model"] = model
            if base_url:
                settings["base_url"] = base_url
            elif preset.get("base_url"):
                settings["base_url"] = preset["base_url"]
            if preset.get("api_key_env"):
                settings["api_key_env"] = preset["api_key_env"]

            # Register the plugin marketplace from GitHub
            settings.setdefault("extraKnownMarketplaces", {})
            settings["extraKnownMarketplaces"]["scitech"] = {
                "source": {
                    "source": "github",
                    "repo": MARKETPLACE_REPO,
                }
            }

            # Enable plugins by default
            settings.setdefault("enabledPlugins", {})
            for plugin_name in DEFAULT_PLUGINS:
                settings["enabledPlugins"][plugin_name] = True

            self._write_settings(settings_path, settings)
            log.info(
                "claude_code_settings_written",
                path=settings_path,
                provider=provider,
                model=model,
                plugins=DEFAULT_PLUGINS,
            )

    def uninstall(self, workspace: str) -> None:
        for target in ("CLAUDE.md", ".mcp.json", ".claude"):
            path = os.path.join(workspace, target)
            if os.path.isfile(path):
                os.remove(path)
                log.info("claude_code_file_removed", path=path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
                log.info("claude_code_dir_removed", path=path)

        # Global cleanup — remove only what we added, leaving any user-owned
        # global config intact.
        remove_managed_block(home_path(".claude", "CLAUDE.md"))
        server_names = ["pegasus-docs", "kiso-docs"]
        mcp_src = os.path.join(KNOWLEDGE_ROOT, "mcp", "servers.json")
        if os.path.isfile(mcp_src):
            try:
                with open(mcp_src) as f:
                    server_names = list((json.load(f) or {}).get("mcpServers", {}))
            except (json.JSONDecodeError, OSError):
                pass
        unmerge_json_mapping(home_path(".claude.json"), "mcpServers", server_names)
        global_settings = home_path(".claude", "settings.json")
        unmerge_json_mapping(global_settings, "extraKnownMarketplaces", ["scitech"])
        unmerge_json_mapping(global_settings, "enabledPlugins", DEFAULT_PLUGINS)
