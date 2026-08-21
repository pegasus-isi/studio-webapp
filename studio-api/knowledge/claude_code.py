"""ClaudeCodeAdapter — knowledge adapter for Claude Code.

Install: copy CLAUDE.md, write a filtered .mcp.json, register plugins via
settings.json. Writes settings.json for LLM provider config + marketplace/plugin
registration.

The tree's ``.mcp.json`` is not copied verbatim: the checkout tracks a moving
branch, and an ``mcpServers`` entry with a ``command`` is a process Claude Code
starts at its next launch. Only https remotes get through (``filter_mcp_servers``).
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any

from urllib.parse import urlsplit

import structlog

from knowledge import layout, seed
from knowledge.adapters import KnowledgeAdapter
from knowledge.common import (
    build_knowledge_appendix,
    ensure_context_file,
    home_path,
    merge_json_mapping,
    remove_generated_context_file,
    remove_json_if_empty,
    remove_managed_block,
    rmdir_if_empty,
    unmerge_json_mapping,
    unmerge_json_top,
    upsert_managed_block,
)
from llm.providers import PROVIDERS, provider_type

log = structlog.get_logger()


# Marketplace repo for plugin registration
MARKETPLACE_REPO = "pegasus-isi/claude-plugin-marketplace"

# Plugins to enable by default
DEFAULT_PLUGINS = [
    "pegasus-ai@scitech",
    "pegasus-dev@scitech",
]

# Transports Claude Code accepts for a remote server; "streamable-http" is its
# documented alias for "http". Everything else — a stdio entry (`command`),
# "ws", or a `url` with no `type` (which Claude Code reads as stdio) — is
# dropped. The tree this comes from is pulled from a moving branch, so its
# .mcp.json is data we did not write, not config we own.
_REMOTE_MCP_TYPES = frozenset({"http", "sse", "streamable-http"})

# The only keys forwarded. `headers`, `oauth` and friends are subject to ${VAR}
# expansion from the user's environment, which would turn an https URL into a
# place to send API keys.
_REMOTE_MCP_KEYS = ("type", "url")


def _mcp_reject_reason(spec: Any) -> str | None:
    """Why ``spec`` may not be handed to Claude Code, or None if it may."""
    if not isinstance(spec, dict):
        return "not a mapping"
    for key in ("command", "args", "env"):
        if key in spec:
            return f"has {key!r} (stdio server)"
    if spec.get("type") not in _REMOTE_MCP_TYPES:
        return f"type {spec.get('type')!r} is not an https remote"
    url = spec.get("url")
    if not isinstance(url, str) or not url:
        return "no url"
    if "$" in url:
        return "url contains an environment expansion"
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return "url is not https://"
    if parts.username or parts.password:
        return "url carries credentials"
    return None


def filter_mcp_servers(servers: Any, *, source: str = "") -> dict[str, dict[str, Any]]:
    """Keep only https remote MCP servers from a tree's ``mcpServers`` mapping.

    A stdio entry is a process Claude Code starts at its next launch — with no
    prompt at user scope, and none at project scope for ``claude -p``. A remote
    entry is an HTTPS connection to a fixed URL and nothing more, and that is
    the only shape allowed through. Each drop is logged with its name, so a
    legitimate server rejected by mistake is visible rather than silent.
    """
    kept: dict[str, dict[str, Any]] = {}
    if not isinstance(servers, dict):
        log.warning("mcp_servers_not_a_mapping", source=source)
        return kept
    for name, spec in servers.items():
        reason = _mcp_reject_reason(spec)
        if reason:
            log.warning(
                "mcp_server_dropped", server=str(name), reason=reason, source=source,
            )
            continue
        kept[str(name)] = {k: spec[k] for k in _REMOTE_MCP_KEYS}
    return kept


def _retract_previously_merged(
    path: str, declared: dict[str, Any], dropped: list[str],
) -> None:
    """Take back entries an earlier, unfiltered build merged into ~/.claude.json.

    Only an entry identical to the tree's is ours to remove: a user's own
    server that happens to share a name is left alone.
    """
    try:
        with open(path) as f:
            current = (json.load(f) or {}).get("mcpServers") or {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return
    if not isinstance(current, dict):
        return
    ours = [n for n in dropped if n in current and current[n] == declared.get(n)]
    if ours:
        unmerge_json_mapping(path, "mcpServers", ours)
        log.warning("claude_code_mcp_servers_retracted", path=path, servers=ours)


def _marketplace_source() -> tuple[str, dict[str, Any]]:
    """(marketplace name, Claude Code source spec) for the tree in use.

    A ``directory`` source is read in place and never refreshed over the
    network, so the pod stays on the commit its image pinned.
    """
    root = layout.marketplace_root()
    try:
        return seed.marketplace_name(root), {"source": "directory", "path": root}
    except (OSError, KeyError, ValueError) as exc:
        log.info(
            "claude_code_marketplace_fallback_github",
            checkout=root,
            error=str(exc),
        )
        return "scitech", {"source": "github", "repo": MARKETPLACE_REPO}


class ClaudeCodeAdapter(KnowledgeAdapter):
    """Translates canonical knowledge into Claude Code's native format."""

    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        # Copy CLAUDE.md from references and append skills/agents appendix.
        content = layout.context_document()
        if content:
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

        # Write .mcp.json (project scope, $HOME) and merge the same servers
        # into ~/.claude.json (user scope). Both go through filter_mcp_servers:
        # the tree is pulled from a branch, and only https remotes may reach a
        # file that Claude Code will act on without asking. The project file is
        # always rewritten — an empty mapping rather than nothing — so a copy
        # made verbatim by an earlier build is overwritten on the next start.
        mcp_src = layout.mcp_servers_path()
        mcp_dst = os.path.join(workspace, ".mcp.json")
        if mcp_src and os.path.isfile(mcp_src):
            declared: dict[str, Any] = {}
            try:
                with open(mcp_src) as f:
                    raw = (json.load(f) or {}).get("mcpServers", {})
                declared = raw if isinstance(raw, dict) else {}
            except (json.JSONDecodeError, OSError) as e:
                log.warning("claude_code_mcp_source_unreadable", path=mcp_src, error=str(e))
            servers = filter_mcp_servers(declared, source=mcp_src)
            dropped = [n for n in declared if n not in servers]
            try:
                with open(mcp_dst, "w") as f:
                    json.dump({"mcpServers": servers}, f, indent=2)
                if servers:
                    merge_json_mapping(home_path(".claude.json"), "mcpServers", servers)
                if dropped:
                    _retract_previously_merged(home_path(".claude.json"), declared, dropped)
            except OSError as e:
                log.warning("claude_code_global_mcp_failed", error=str(e))
            log.info(
                "claude_code_mcp_json_written",
                path=mcp_dst, servers=sorted(servers), dropped=dropped,
            )

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
        # Runs on every start, so it is also where a deleted CLAUDE.md comes back.
        ensure_context_file(os.path.join(workspace, "CLAUDE.md"))
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

            # Register the plugin marketplace. Prefer the local checkout: every
            # other tool reads it, so sourcing Claude Code from GitHub instead
            # would put one tool on marketplace main while the rest sat on the
            # pinned commit — the same skills, two versions, which is the split
            # this whole arrangement exists to prevent. Falls back to GitHub
            # where there is no checkout (a dev laptop, say).
            name, source = _marketplace_source()
            settings.setdefault("extraKnownMarketplaces", {})
            settings["extraKnownMarketplaces"][name] = {"source": source}

            # Enable plugins by default. The marketplace name is read from the
            # manifest rather than assumed: Claude Code enforces that the key
            # matches it, and reports a mismatch as "plugin not found".
            settings.setdefault("enabledPlugins", {})
            for plugin_name in DEFAULT_PLUGINS:
                plugin = plugin_name.split("@", 1)[0]
                settings["enabledPlugins"][f"{plugin}@{name}"] = True

            self._write_settings(settings_path, settings)
            log.info(
                "claude_code_settings_written",
                path=settings_path,
                provider=provider,
                model=model,
                plugins=DEFAULT_PLUGINS,
            )

    @staticmethod
    def _mcp_server_names() -> list[str]:
        """Servers we merged into ~/.claude.json / .mcp.json.

        The defaults are unioned in, not replaced: a tree that dropped a server
        must still have the entry an older install added taken back out.
        """
        names = {"pegasus-docs", "kiso-docs"}
        mcp_src = layout.mcp_servers_path()
        if mcp_src and os.path.isfile(mcp_src):
            try:
                with open(mcp_src) as f:
                    names |= set((json.load(f) or {}).get("mcpServers", {}))
            except (json.JSONDecodeError, OSError):
                pass
        return sorted(names)

    def _remove_project_mcp_json(self, path: str) -> None:
        """``.mcp.json`` was a copy of the tree file. Remove it if it still is;
        otherwise take out only our servers, and the file only if nothing
        remains — at $HOME the user may have added their own servers to it."""
        if os.path.islink(path) or not os.path.isfile(path):
            return
        try:
            with open(path) as f:
                current = json.load(f)
        except (json.JSONDecodeError, OSError):
            return  # not something we can reason about — leave it
        mcp_src = layout.mcp_servers_path()
        if mcp_src and os.path.isfile(mcp_src):
            try:
                with open(mcp_src) as f:
                    if json.load(f) == current:
                        os.remove(path)
                        log.info("claude_code_file_removed", path=path)
                        return
            except (json.JSONDecodeError, OSError):
                pass
        unmerge_json_mapping(path, "mcpServers", self._mcp_server_names())
        remove_json_if_empty(path)

    def uninstall(self, workspace: str) -> None:
        # ``workspace`` is $HOME, so ``.claude`` here is the user's real Claude
        # Code state — credentials, transcripts, their settings. Everything
        # below removes exactly what install() wrote, one file or key at a
        # time; the directory is removed only if that left it empty.
        remove_generated_context_file(os.path.join(workspace, "CLAUDE.md"))
        self._remove_project_mcp_json(os.path.join(workspace, ".mcp.json"))

        remove_managed_block(home_path(".claude", "CLAUDE.md"))

        claude_json = home_path(".claude.json")
        unmerge_json_mapping(claude_json, "mcpServers", self._mcp_server_names())
        remove_json_if_empty(claude_json)

        # Keys are derived the same way update_llm_config() derives them, plus
        # the legacy literal, so a marketplace renamed since install is still
        # unregistered.
        market_name, _ = _marketplace_source()
        market_names = sorted({market_name, "scitech"})
        plugin_keys = sorted(
            f"{p.split('@', 1)[0]}@{m}" for p in DEFAULT_PLUGINS for m in market_names
        )
        for settings_path in sorted({
            os.path.join(workspace, ".claude", "settings.json"),
            home_path(".claude", "settings.json"),
        }):
            unmerge_json_mapping(settings_path, "extraKnownMarketplaces", market_names)
            unmerge_json_mapping(settings_path, "enabledPlugins", plugin_keys)
            unmerge_json_top(settings_path, ["provider", "model", "base_url", "api_key_env"])

        rmdir_if_empty(os.path.join(workspace, ".claude"))
        rmdir_if_empty(home_path(".claude"))
        log.info("claude_code_uninstalled", workspace=workspace)
