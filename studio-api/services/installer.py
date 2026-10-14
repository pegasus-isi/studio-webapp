"""Tool installer — handles npm install and knowledge adapter calls."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any

import structlog

from knowledge.adapters import get_adapter
from knowledge.common import config_root, prune_legacy_workspace_copies
from services.procutil import communicate_or_kill
from services.tool_env import npm_prefix, with_tool_paths
from workspace import legacy_workspace_root, workspace_root

log = structlog.get_logger()

WORKSPACE_ROOT = workspace_root()

# Tool registry loaded from static JSON
_TOOL_REGISTRY: list[dict[str, Any]] | None = None

_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "tool_registry.json"
)


def _install_error(tool_id: str, prefix: str, stderr: bytes) -> str:
    """Build a user-facing install error, keeping the useful tail of stderr.

    npm/curl print the actionable summary last, so we keep the tail rather than
    the head, and add a hint for the most common failure (no write permission).
    """
    text = stderr.decode("utf-8", errors="replace").strip()
    tail = text[-800:] if len(text) > 800 else text
    msg = f"Install of '{tool_id}' failed: {tail}" if tail else (
        f"Install of '{tool_id}' failed (no output)."
    )
    if "EACCES" in text or "permission denied" in text.lower():
        msg += (
            f"\n\nHint: could not write to the npm prefix ({prefix}). The image's "
            "/opt/npm-global is root-owned; the notebook needs a HOME-writable "
            "prefix (~/.npm-global) on PATH."
        )
    return msg


def _load_registry() -> list[dict[str, Any]]:
    """Load the static tool registry JSON."""
    global _TOOL_REGISTRY
    if _TOOL_REGISTRY is not None:
        return _TOOL_REGISTRY

    if os.path.isfile(_REGISTRY_PATH):
        with open(_REGISTRY_PATH) as f:
            data = json.load(f)
        _TOOL_REGISTRY = data.get("tools", [])
    else:
        # Inline fallback registry
        _TOOL_REGISTRY = _INLINE_REGISTRY

    return _TOOL_REGISTRY


def get_tool_info(tool_id: str) -> dict[str, Any] | None:
    """Look up a tool by ID from the registry."""
    for tool in _load_registry():
        if tool["id"] == tool_id:
            return tool
    return None


def get_all_tools() -> list[dict[str, Any]]:
    return _load_registry()


def _top_level_dirs(root: str) -> set[str]:
    try:
        return {n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n))}
    except OSError:
        return set()


class ToolInstaller:
    """Manages tool installation and uninstallation."""

    async def install(self, tool_id: str, db: Any) -> str:
        """Install a tool: check binary, npm install, call adapter, update DB."""
        info = get_tool_info(tool_id)
        if info is None:
            raise ValueError(f"Unknown tool: {tool_id}")

        # Built-in tools don't need npm install
        if info.get("install_method") == "builtin":
            await db.install_tool(tool_id)
            log.info("tool_installed", tool_id=tool_id, method="builtin")
            return "installed"

        binary = info.get("binary")
        install_command = info.get("install_command")

        # Check if binary already exists
        if binary and not shutil.which(binary):
            if install_command:
                log.info("tool_installing", tool_id=tool_id, command=install_command)
                # Build the install environment. The image's /opt/npm-global is
                # root-owned, so an unprivileged notebook can't `npm install -g`
                # into it (EACCES). Install into a HOME-writable prefix instead,
                # which also persists on the PVC. `set -o pipefail` so a failed
                # `curl … | bash` install (antigravity) surfaces instead of being
                # masked by bash's exit 0.
                prefix = npm_prefix()
                install_env = with_tool_paths(os.environ.copy())
                install_env["NPM_CONFIG_PREFIX"] = prefix
                os.makedirs(os.path.join(prefix, "bin"), exist_ok=True)

                proc = await asyncio.create_subprocess_exec(
                    "bash",
                    "-o",
                    "pipefail",
                    "-c",
                    install_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=install_env,
                )
                stdout, stderr = await communicate_or_kill(proc, 300)
                if proc.returncode != 0:
                    raise RuntimeError(_install_error(tool_id, prefix, stderr))
                # Update process PATH + prefix so subsequent shutil.which() calls
                # and terminals spawned by this process find the new binary.
                os.environ["PATH"] = with_tool_paths(dict(os.environ))["PATH"]
                os.environ.setdefault("NPM_CONFIG_PREFIX", prefix)

        # Call knowledge adapter
        llm_config = await db.get_llm_config() or {}
        adapter = get_adapter(tool_id)
        if adapter:
            prune_legacy_workspace_copies(legacy_workspace_root(WORKSPACE_ROOT))
            adapter.install(config_root(), llm_config)

        # Update DB
        await db.install_tool(tool_id)
        log.info("tool_installed", tool_id=tool_id)
        return "installed"

    async def uninstall(self, tool_id: str, db: Any) -> str:
        """Uninstall a tool: call adapter.uninstall, remove from DB.

        Unknown ids raise (the router maps that to 404). A tool that is not
        installed is left alone on disk: the endpoint is a path-only POST with
        no same-origin gate yet (PAS-SEC-01), so it has to be harmless when it
        was not meant.
        """
        if get_tool_info(tool_id) is None:
            raise ValueError(f"Unknown tool: {tool_id}")
        if await db.get_tool(tool_id) is None:
            log.info("tool_uninstall_skipped", tool_id=tool_id, reason="not_installed")
            return "not_installed"

        adapter = get_adapter(tool_id)
        if adapter:
            # Adapters receive $HOME and remove only the individual files they
            # generated. The tools' own directories (~/.claude, ~/.codex,
            # ~/.agents, ~/.antigravity, ~/.config/opencode) hold credentials
            # and history and are never removed. The before/after check is a
            # tripwire: it cannot undo a deletion, but it makes one loud.
            before = _top_level_dirs(config_root())
            adapter.uninstall(config_root())
            prune_legacy_workspace_copies(legacy_workspace_root(WORKSPACE_ROOT))
            gone = sorted(before - _top_level_dirs(config_root()))
            if gone:
                log.error("tool_uninstall_removed_top_level_dirs", tool_id=tool_id, dirs=gone)

        await db.remove_tool(tool_id)
        log.info("tool_uninstalled", tool_id=tool_id)
        return "uninstalled"


# Inline fallback registry (used when tool_registry.json doesn't exist).
# Order here is the Workbench/Settings display order. `paid: True` flags tools
# that run on the user's own LLM subscription or API tokens (shown as a
# "Your subscription" badge: "Paid" read as if Studio charged for the tool,
# #515).
# JupyterLab (Notebooks nav) and PegasusAI Chat (built-in slide-in) are
# intentionally NOT marketplace tools — they have dedicated entry points and
# aren't installable AI CLIs.
_INLINE_REGISTRY: list[dict[str, Any]] = [
    {
        "id": "opencode",
        "name": "OpenCode",
        "vendor": "opencode-ai",
        "description": "Open-source AI coding assistant with web UI, agents, and skills",
        "version_command": "opencode version",
        "install_method": "npm",
        "install_command": "npm install -g opencode-ai",
        "binary": "opencode",
        "type": "web",
        # Loopback only: nginx is the sole exposed port (8888) and nothing in
        # the product links to this port directly. Binding 0.0.0.0 put the
        # tool UI — the user's files and LLM keys — on the pod's cluster IP,
        # reachable by any pod on a flat network with no NetworkPolicy.
        "web_command": "opencode web --port {port} --hostname 127.0.0.1",
        "supports_mcp": True,
        "supports_web": True,
        "required_env": [],
        "paid": False,
        "knowledge_adapter": "opencode",
        "icon": "opencode.svg",
        "homepage": "https://opencode.ai",
    },
    {
        "id": "antigravity",
        "name": "Antigravity CLI",
        "vendor": "Google",
        "description": "Google's agentic coding CLI powered by Gemini — subagents, tool use, and code execution",
        # The installer drops the CLI as `agy` (not `antigravity`) into ~/.local/bin.
        "version_command": "agy --version",
        "install_method": "shell",
        "install_command": "curl -fsSL https://antigravity.google/cli/install.sh | bash",
        "binary": "agy",
        "type": "terminal",
        "supports_mcp": False,
        "supports_web": False,
        "required_env": ["GOOGLE_API_KEY"],
        "paid": True,
        "knowledge_adapter": "antigravity",
        "icon": "antigravity.svg",
        "homepage": "https://antigravity.google",
    },
    {
        "id": "codex-cli",
        "name": "Codex CLI",
        "vendor": "OpenAI",
        "description": "OpenAI's coding agent for terminal-based development",
        "version_command": "codex --version",
        "install_method": "npm",
        "install_command": "npm install -g @openai/codex",
        "binary": "codex",
        "type": "terminal",
        "supports_mcp": False,
        "supports_web": False,
        "required_env": ["OPENAI_API_KEY"],
        "paid": True,
        "knowledge_adapter": "codex_cli",
        "icon": "codex.svg",
        "homepage": "https://github.com/openai/codex",
    },
    {
        "id": "crush",
        "name": "Crush",
        "vendor": "Charmbracelet",
        "description": "Terminal AI coding agent with multi-provider support, MCP servers, and skills",
        "version_command": "crush --version",
        "install_method": "npm",
        "install_command": "npm install -g @charmland/crush",
        "binary": "crush",
        "type": "terminal",
        "supports_mcp": True,
        "supports_web": False,
        # Keyless-capable: the built-in LLM Gateway is written to crush.json,
        # so Crush works with no user API key (hence no "Paid" badge).
        "required_env": [],
        "paid": False,
        "knowledge_adapter": "crush",
        "icon": "crush.svg",
        "homepage": "https://github.com/charmbracelet/crush",
    },
    {
        "id": "claude-code",
        "name": "Claude Code",
        "vendor": "Anthropic",
        "description": "AI coding assistant with plugin skills, MCP servers, and agentic tool use",
        "version_command": "claude --version",
        "install_method": "npm",
        "install_command": "npm install -g @anthropic-ai/claude-code",
        "binary": "claude",
        "type": "terminal",
        "supports_mcp": True,
        "supports_web": False,
        "required_env": ["ANTHROPIC_API_KEY"],
        "paid": True,
        "knowledge_adapter": "claude_code",
        "icon": "claude.svg",
        "homepage": "https://docs.anthropic.com/en/docs/claude-code",
    },
]
