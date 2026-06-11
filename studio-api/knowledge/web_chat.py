"""WebChatAdapter — knowledge adapter for built-in PegasusAI Chat.

No-op adapter: the built-in chat loads knowledge server-side directly
from the knowledge store, so no workspace files need to be written.
"""

from __future__ import annotations

from typing import Any

from knowledge.adapters import KnowledgeAdapter


class WebChatAdapter(KnowledgeAdapter):
    """Built-in chat loads knowledge server-side — no workspace changes needed."""

    def install(self, workspace: str, llm_config: dict[str, Any]) -> None:
        pass  # Knowledge is loaded in-memory by the chat router

    def update_llm_config(self, workspace: str, llm_config: dict[str, Any]) -> None:
        pass  # Chat router reads LLM config from DB directly

    def uninstall(self, workspace: str) -> None:
        pass  # Nothing to clean up
