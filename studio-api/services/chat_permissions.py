"""The chat command-approval setting (SEC-4).

One stored flag: whether the assistant must ask before running a shell
command (and, with #596, before writing outside ``~/workflows`` and
``~/data``). The OpenCode engine enforces it as ``permission`` rules in its
generated config (``knowledge/opencode.py``), written by the LLM propagator;
the engine's own permission prompts are what the user answers.

The per-turn approval back-channel that the builtin agent loop needed (an
in-memory future per tool call, resolved by ``POST /api/chat/permission``) was
removed with that loop in #664.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger()

_SETTING_KEY = "chat_require_command_approval"


async def require_approval(db: Any) -> bool:
    """Whether shell commands must be confirmed. Defaults to True (secure).

    Any DB hiccup fails safe to requiring approval rather than silently
    reverting to auto-run.
    """
    try:
        raw = await db.get_setting(_SETTING_KEY, "1")
    except Exception as e:
        log.warning("approval_setting_lookup_failed", error=str(e))
        return True
    return str(raw).lower() not in ("0", "false", "no", "off")


async def set_require_approval(db: Any, value: bool) -> None:
    await db.set_setting(_SETTING_KEY, "1" if value else "0")
