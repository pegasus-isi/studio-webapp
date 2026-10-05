"""AI-help feedback episodes: prompts, answers and give-up tags.

The logic lives in ``services/feedback_episodes.py``; this is the browser's
view of it. Every ``{id}`` accepts an episode id *or* a run id, because what
the browser knows is the run it is looking at or the run the chat is scoped
to, not the episode the watch loop opened for it.

Not to be confused with ``/api/feedback/message`` (per-reply ratings), which
lives in its own router.
"""

from __future__ import annotations

from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services import feedback_episodes

log = structlog.get_logger()

router = APIRouter(prefix="/api/feedback/episodes", tags=["feedback"])

Answer = Literal[
    "fixed", "partly", "no_help", "did_not_try", "fixed_myself", "ask_ai", "dismissed"
]


class AskedRequest(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=200)
    # True for a chat turn actually sent while scoped to the run; False for the
    # button that only opens the chat with a draft.
    turn: bool = False


class AnswerRequest(BaseModel):
    answer: Answer
    comment: str | None = Field(default=None, max_length=4000)
    session: str | None = Field(default=None, max_length=100)


class GiveUpRequest(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=200)
    episode_id: str | None = Field(default=None, max_length=200)
    reason: Literal["stop", "error"]
    tag: Literal["wrong_direction", "too_slow", "error", "other"]


@router.get("/pending")
async def get_pending(
    session: str = Query(..., min_length=1, max_length=100),
) -> dict:
    """At most one prompt for this browser session, after the fatigue rules.

    The watch loop pokes browsers over the notification stream when a prompt
    becomes due; this is what they then call — and what they poll, so a poke
    lost to a dropped stream only delays a prompt rather than losing it.
    ``prompts_enabled`` lets the chat hide its give-up row under the same
    switches (env, mute, snooze) without a second endpoint.
    """
    from main import db

    prompt = await feedback_episodes.pending(db, session)
    blocked = await feedback_episodes.prompt_block_reason(db)
    return {"prompt": prompt, "prompts_enabled": blocked is None}


@router.post("/giveup")
async def post_giveup(req: GiveUpRequest) -> dict:
    """A give-up tag from the row under a stopped or errored chat turn."""
    from main import db

    result = await feedback_episodes.record_giveup(
        db,
        conversation_id=req.conversation_id,
        episode_key=req.episode_id,
        reason=req.reason,
        tag=req.tag,
    )
    return {"status": "ok", **result}


@router.post("/{episode_id}/asked")
async def post_asked(episode_id: str, req: AskedRequest) -> dict:
    """Mark that the AI was asked about this episode (or this run's).

    404 for a run with no episode that isn't known to have failed — the chat
    calls this on every turn scoped to a run without knowing its status, so
    the caller ignores it.
    """
    from main import db

    episode = await feedback_episodes.mark_asked(
        db, episode_id, conversation_id=req.conversation_id, turn=req.turn
    )
    if episode is None:
        raise HTTPException(status_code=404, detail="No failed run to link")
    return {
        "status": "ok",
        "episode_id": episode["id"],
        "asked_ai_at": episode.get("asked_ai_at"),
        "ai_turns": episode.get("ai_turns") or 0,
    }


@router.post("/{episode_id}/answer")
async def post_answer(episode_id: str, req: AnswerRequest) -> dict:
    from main import db

    episode = await feedback_episodes.answer(
        db, episode_id, answer=req.answer, comment=req.comment
    )
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return {"status": "ok", "episode_id": episode["id"], "answer": episode["answer"]}
