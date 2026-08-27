"""Per-turn command approval back-channel for chat (SEC-4).

The chat response is a one-way SSE stream, so pausing a turn to ask the user
whether a shell command may run needs a side channel: the streaming generator
emits a ``permission_request`` event and then awaits a decision here; the
``POST /api/chat/permission`` endpoint resolves it.

Single-process, in-memory — the same assumption ``_active_clients`` in
``routers/chat.py`` already makes (one uvicorn worker under supervisord). The
futures are event-loop bound; every FastAPI handler runs on the one loop, so
the endpoint resolving a future the stream created is safe.

Default posture is prompt-on-every-command; an operator/user can switch to
auto-approve (the old behavior) via the ``chat_require_command_approval``
setting.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import structlog

log = structlog.get_logger()

Decision = Literal["approve", "deny", "timeout"]

_SETTING_KEY = "chat_require_command_approval"
# Long enough to read a command and decide, short enough that a walked-away
# user doesn't wedge the turn forever (timeout is treated as denial). Kept
# under nginx's 300s proxy_read_timeout on the SSE path so a timeout-deny
# fires and the stream resumes before nginx would close it mid-turn.
_DECISION_TIMEOUT_S = 240.0

# key -> pending future resolved by the /permission endpoint.
_pending: dict[str, asyncio.Future[Decision]] = {}


def _key(request_id: str, call_id: str) -> str:
    # NUL can't appear in either id (uuid / tool call id), so it's an
    # unambiguous separator.
    return f"{request_id}\x00{call_id}"


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


async def await_decision(request_id: str, call_id: str) -> Decision:
    """Block until the user approves/denies *call_id*, or the wait times out.

    Returns ``"approve"``, ``"deny"``, or ``"timeout"`` (caller treats the
    last two as "do not run"). Registering under both ids means a Stop on the
    turn (``deny_all_for``) also releases this wait.
    """
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[Decision] = loop.create_future()
    key = _key(request_id, call_id)
    _pending[key] = fut
    try:
        return await asyncio.wait_for(fut, timeout=_DECISION_TIMEOUT_S)
    except asyncio.TimeoutError:
        return "timeout"
    finally:
        _pending.pop(key, None)


def resolve(request_id: str, call_id: str, decision: Decision) -> bool:
    """Resolve a pending request from the endpoint. True if one was waiting."""
    fut = _pending.get(_key(request_id, call_id))
    if fut is not None and not fut.done():
        fut.set_result(decision)
        return True
    return False


def deny_all_for(request_id: str) -> int:
    """Deny every pending request for a turn — called when the turn is stopped
    so an in-flight approval prompt doesn't leave the stream hung."""
    prefix = f"{request_id}\x00"
    n = 0
    for key, fut in list(_pending.items()):
        if key.startswith(prefix) and not fut.done():
            fut.set_result("deny")
            n += 1
    return n
