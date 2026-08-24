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
            prune_legacy_workspace_copies(WORKSPACE_ROOT)
            adapter.install(config_root(), llm_config)

        # Update DB
        await db.install_tool(tool_id)
        log.info("tool_installed", tool_id=tool_id)
        return "installed"

    async def uninstall(self, tool_id: str, db: Any) -> str:
        """Uninstall a tool: call adapter.uninstall, remove from DB."""
        adapter = get_adapter(tool_id)
        if adapter:
            # Config lives at the config root now; also clear anything an older
            # build left in the workspace so uninstall really removes it all.
            adapter.uninstall(config_root())
            prune_legacy_workspace_copies(WORKSPACE_ROOT)

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
        "id": "pi",
        "name": "Pi",
        "vendor": "Earendil Works",
        "description": "Minimal, extensible AI coding agent with skills and custom OpenAI-compatible providers",
        "version_command": "pi --version",
        "install_method": "npm",
        # --ignore-scripts is the vendor-documented install (skips postinstall
        # hooks), which also fits Studio's unprivileged pods.
        "install_command": "npm install -g --ignore-scripts @earendil-works/pi-coding-agent",
        "binary": "pi",
        "type": "terminal",
        "supports_mcp": False,
        "supports_web": False,
        # Keyless-capable: the built-in LLM Gateway is written to models.json,
        # so pi works with no user API key (hence no "Paid" badge).
        "required_env": [],
        "paid": False,
        "knowledge_adapter": "pi",
        "icon": "pi.svg",
        "homepage": "https://pi.dev",
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
