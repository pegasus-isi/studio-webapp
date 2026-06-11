"""Knowledge adapter ABC and registry.

Each AI tool has different conventions for consuming domain knowledge.
The KnowledgeAdapter pattern translates canonical knowledge into each
tool's native format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import structlog

log = structlog.get_logger()


class KnowledgeAdapter(ABC):
    """Translates canonical knowledge into a tool's native format."""

    @abstractmethod
    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Full install: context + skills + agents + MCP."""

    @abstractmethod
    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        """Update LLM provider/model/key for this tool."""

    @abstractmethod
    def uninstall(self, workspace: str) -> None:
        """Remove tool-specific config from workspace."""


# Lazy adapter registry — avoids circular imports
_ADAPTER_MAP: dict[str, type[KnowledgeAdapter]] | None = None


def _build_registry() -> dict[str, type[KnowledgeAdapter]]:
    from knowledge.antigravity import AntigravityAdapter
    from knowledge.claude_code import ClaudeCodeAdapter
    from knowledge.codex_cli import CodexCLIAdapter
    from knowledge.jupyterlab import JupyterLabAdapter
    from knowledge.opencode import OpenCodeAdapter
    from knowledge.web_chat import WebChatAdapter

    return {
        "claude-code": ClaudeCodeAdapter,
        "codex-cli": CodexCLIAdapter,
        "opencode": OpenCodeAdapter,
        "antigravity": AntigravityAdapter,
        "pegasus-ai-chat": WebChatAdapter,
        "jupyterlab": JupyterLabAdapter,
    }


def get_adapter(tool_id: str) -> KnowledgeAdapter | None:
    """Return an adapter instance for the given tool, or None."""
    global _ADAPTER_MAP
    if _ADAPTER_MAP is None:
        _ADAPTER_MAP = _build_registry()

    cls = _ADAPTER_MAP.get(tool_id)
    if cls is None:
        return None
    return cls()
