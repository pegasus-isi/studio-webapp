"""Tool installer — handles npm install and knowledge adapter calls."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any

import structlog

from knowledge.adapters import get_adapter

log = structlog.get_logger()

WORKSPACE_ROOT = os.environ.get(
    "STUDIO_WORKSPACE_DIR",
    os.path.join(os.environ.get("HOME", "/tmp"), "work"),
)

# Tool registry loaded from static JSON
_TOOL_REGISTRY: list[dict[str, Any]] | None = None

_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "tool_registry.json"
)


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
                # Build install environment
                install_env = os.environ.copy()
                npm_prefix = os.environ.get("NPM_CONFIG_PREFIX", "/opt/npm-global")
                install_env["NPM_CONFIG_PREFIX"] = npm_prefix
                extra_bins = [os.path.join(npm_prefix, "bin")]
                home = os.environ.get("HOME", "")
                if home:
                    extra_bins.append(os.path.join(home, ".local", "bin"))
                for bp in extra_bins:
                    if bp not in install_env.get("PATH", ""):
                        install_env["PATH"] = f"{bp}:{install_env.get('PATH', '')}"

                proc = await asyncio.create_subprocess_shell(
                    install_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=install_env,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=300,
                )
                if proc.returncode != 0:
                    err = stderr.decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(f"Install failed: {err}")
                # Update process PATH so subsequent shutil.which() calls find the binary
                for bp in extra_bins:
                    if bp not in os.environ.get("PATH", ""):
                        os.environ["PATH"] = f"{bp}:{os.environ.get('PATH', '')}"

        # Call knowledge adapter
        llm_config = await db.get_llm_config() or {}
        adapter = get_adapter(tool_id)
        if adapter:
            adapter.install(WORKSPACE_ROOT, llm_config)

        # Update DB
        await db.install_tool(tool_id)
        log.info("tool_installed", tool_id=tool_id)
        return "installed"

    async def uninstall(self, tool_id: str, db: Any) -> str:
        """Uninstall a tool: call adapter.uninstall, remove from DB."""
        adapter = get_adapter(tool_id)
        if adapter:
            adapter.uninstall(WORKSPACE_ROOT)

        await db.remove_tool(tool_id)
        log.info("tool_uninstalled", tool_id=tool_id)
        return "uninstalled"


# Inline fallback registry (used when tool_registry.json doesn't exist).
# Order here is the Workbench/Settings display order. `paid: True` flags tools
# that run on the user's own LLM API tokens (shown as a "Paid" badge).
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
        "web_command": "opencode web --port {port} --hostname 0.0.0.0",
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
        "version_command": "antigravity --version",
        "install_method": "shell",
        "install_command": "curl -fsSL https://antigravity.google/cli/install.sh | bash",
        "binary": "antigravity",
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
