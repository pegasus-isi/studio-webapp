"""Ratings on OpenCode assistant replies.

OpenCode keeps its transcript in its own store, so its replies have no local row
to rate; they are identified by OpenCode's (session id, message id) instead and
rated here. Only the vote and which model/agent produced the reply are stored,
never the message text: the point is "which models help users", and a
transcript copy would be a second, unmanaged home for whatever users paste
into chat.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

log = structlog.get_logger()

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

_pending: set[asyncio.Task] = set()

# Ids are OpenCode's opaque strings ("ses_…", "msg_…"); model/agent are short
# names. The caps only stop a broken or hostile client from parking megabytes
# in a table nobody prunes.
_Id = Annotated[str, Field(min_length=1, max_length=256)]
_Label = Annotated[str, Field(max_length=256)]


class MessageFeedbackRequest(BaseModel):
    # Strict so JSON `true`, `1.0` or "1" is a 422 rather than silently
    # becoming a 👍: a client that sends those has a bug worth surfacing.
    # (`Literal[1, -1, 0]` alone is not enough; it matches by equality, and
    # `True == 1`.)
    model_config = ConfigDict(strict=True)

    session_id: _Id
    message_id: _Id
    conversation_id: _Label | None = None
    model: _Label | None = None
    agent: _Label | None = None
    # 1 = helpful, -1 = not helpful, 0 = clear the rating.
    rating: StrictInt

    @field_validator("rating")
    @classmethod
    def _known_rating(cls, v: int) -> int:
        if v not in (1, -1, 0):
            raise ValueError("rating must be 1, -1 or 0")
        return v


class MessageFeedbackResponse(BaseModel):
    ok: bool


@router.post("/message", response_model=MessageFeedbackResponse)
async def rate_message(body: MessageFeedbackRequest) -> MessageFeedbackResponse:
    """Rate one assistant reply 👍 (1) / 👎 (-1), or un-rate it (0).

    Idempotent: re-sending the same vote leaves one row, and un-rating a reply
    that has no rating still succeeds, so the client can retry freely.
    """
    from main import db

    # Empty strings mean "unknown" from a client that builds the body from
    # optional fields; store them as NULL so the upsert's COALESCE keeps any
    # value an earlier vote did know.
    conversation_id = body.conversation_id or None
    model = body.model or None
    agent = body.agent or None

    if body.rating == 0:
        await db.clear_message_feedback(body.session_id, body.message_id)
    else:
        await db.upsert_message_feedback(
            body.session_id,
            body.message_id,
            body.rating,
            conversation_id=conversation_id,
            model=model,
            agent=agent,
        )
    log.info("message_feedback", rating=body.rating, model=model, agent=agent)
    # To the cluster's telemetry, in the background: the vote is already
    # stored, and a slow or absent collector must not hold up the click.
    from services import rating_telemetry

    task = asyncio.create_task(rating_telemetry.export_vote(
        db, session_id=body.session_id, message_id=body.message_id,
        rating=body.rating, model=model, agent=agent,
    ))
    _pending.add(task)  # the loop holds tasks weakly; keep it until done
    task.add_done_callback(_pending.discard)
    return MessageFeedbackResponse(ok=True)
