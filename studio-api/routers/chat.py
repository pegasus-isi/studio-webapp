"""Chat endpoints the OpenCode-backed panel still needs from studio-api.

The conversation itself runs in ``opencode serve`` and reaches the browser
through ``routers/opencode.py``; the Pegasus tools reach the engine through
``routers/mcp.py``. What is left here is what only Studio knows:

* the Active Context block for the workflow or run a conversation is about,
  which the panel sends as a synthetic prompt part, and
* the command-approval setting, which the generated OpenCode permission rules
  are written from.

The in-process builtin agent loop that used to live here (direct provider
calls, its own turn broker and approval back-channel, its own transcript
table) was removed in #664. Its ``chat_messages`` table is left in the
database, still pruned at startup, rather than dropped: deleting a user's old
transcripts is not something a code change should do silently.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request

log = structlog.get_logger()

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/command-approval")
async def get_command_approval() -> dict:
    """Whether chat pauses for confirmation before running shell commands."""
    from main import db
    from services import chat_permissions

    return {"require_approval": await chat_permissions.require_approval(db)}


@router.put("/command-approval")
async def set_command_approval(request: Request) -> dict:
    """Body: ``{"require_approval": bool}``."""
    from main import db
    from services import chat_permissions

    try:
        body = await request.json()
    except Exception:
        body = {}
    value = bool(body.get("require_approval", True))
    await chat_permissions.set_require_approval(db, value)

    # The OpenCode engine enforces this setting as a `permission` rule in its
    # generated config (#367), so storing it is only half the job: without
    # propagating, Settings said "commands are confirmed" while the engine kept
    # whatever rule it was last given — including `allow` — for the rest of the
    # pod's life.
    try:
        from llm.propagator import LLMPropagator

        await LLMPropagator().propagate(db)
    except Exception as e:
        log.warning("command_approval_propagate_failed", error=str(e))

    return {"require_approval": value}


@router.get("/scope-block")
async def scope_block(type: str, id: str) -> dict[str, str | None]:
    """The Active Context block for a workflow or run, as plain text.

    The agent loop is not ours, so the caller that knows which workflow the
    conversation is about is the panel, and it sends the block as a synthetic
    prompt part — visible to the model, hidden by the renderer, and
    re-resolved every turn so a finished run stops being described as running.

    Returns ``{"block": null}`` for an id that resolves to nothing: no context,
    carry on.
    """
    from services.chat_scope import SCOPE_TYPES, build_scope_block

    if type not in SCOPE_TYPES:
        raise HTTPException(
            status_code=400, detail=f"Unknown scope type: {type}"
        )
    return {"block": await build_scope_block(type, id)}
