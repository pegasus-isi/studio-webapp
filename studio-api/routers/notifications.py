"""Notification endpoints — list/read/dismiss history plus a live SSE stream.

The SSE stream (`GET /api/notifications/stream`) pushes notifications raised by
the :mod:`services.notification_engine` watch loop (or any ``engine.notify``
caller) to the browser in real time. REST endpoints back the bell dropdown's
history and read-state.
"""

from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from models import (
    NotificationCreateRequest,
    NotificationListResponse,
    NotificationPrefs,
    NotificationResponse,
)
from services.notification_engine import engine

log = structlog.get_logger()

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# Heartbeat so proxies (and EventSource) keep the SSE connection open when the
# system is quiet.
_HEARTBEAT_SECS = 25


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    limit: int = 100, unread_only: bool = False
) -> NotificationListResponse:
    from main import db

    rows = await db.list_notifications(limit=limit, unread_only=unread_only)
    unread = await db.unread_notification_count()
    return NotificationListResponse(
        notifications=[NotificationResponse(**r) for r in rows],
        unread=unread,
    )


@router.post("", response_model=NotificationResponse)
async def create_notification(
    req: NotificationCreateRequest,
) -> NotificationResponse:
    """Raise a notification manually (e.g. from the UI or another service)."""
    notification = await engine.notify(
        kind=req.kind,
        title=req.title,
        body=req.body,
        severity=req.severity,
        run_id=req.run_id,
        link=req.link,
        dedup_key=req.dedup_key,
        meta=req.meta,
    )
    if notification is None:
        raise HTTPException(status_code=503, detail="Notification engine not ready")
    return NotificationResponse(**notification)


@router.get("/prefs", response_model=NotificationPrefs)
async def get_prefs() -> NotificationPrefs:
    """Return muted notification categories."""
    return NotificationPrefs(muted=engine.muted_kinds())


@router.put("/prefs", response_model=NotificationPrefs)
async def update_prefs(prefs: NotificationPrefs) -> NotificationPrefs:
    """Set which notification categories are muted (suppressed)."""
    muted = await engine.set_muted_kinds(prefs.muted)
    return NotificationPrefs(muted=muted)


@router.post("/{notification_id}/read")
async def mark_read(notification_id: str) -> dict[str, str]:
    from main import db

    await db.mark_notification_read(notification_id)
    return {"status": "ok"}


@router.post("/read-all")
async def mark_all_read() -> dict[str, str]:
    from main import db

    await db.mark_all_notifications_read()
    return {"status": "ok"}


@router.delete("/{notification_id}")
async def delete_notification(notification_id: str) -> dict[str, str]:
    from main import db

    await db.delete_notification(notification_id)
    return {"status": "ok"}


@router.delete("")
async def clear_notifications() -> dict[str, str]:
    from main import db

    await db.clear_notifications()
    return {"status": "ok"}


@router.get("/stream")
async def stream_notifications() -> StreamingResponse:
    """SSE stream of notifications as they are raised."""
    queue = engine.subscribe()

    async def event_stream():
        try:
            # Open the stream immediately so the client's onopen fires.
            yield ": connected\n\n"
            while True:
                try:
                    notification = await asyncio.wait_for(
                        queue.get(), timeout=_HEARTBEAT_SECS
                    )
                    yield f"data: {json.dumps(notification)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            engine.unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
