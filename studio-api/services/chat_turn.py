"""Keep a chat turn alive when the browser goes away.

A chat turn used to be owned by the HTTP request that started it: the browser
POSTed to `/chat/stream` and read the response body, and if that request ended
— navigating to another page, closing the tab, a dropped connection — the
generator was cancelled mid-sentence and the answer was lost.

That bites hardest during exactly the wait it creates. Studio's left nav is
plain `<a>` links (Next's `basePath` is build-time only and the app runs under
a dynamic `/user/<name>` prefix), so every nav click is a full page load, and
clicking one while the assistant is typing threw the reply away.

This module puts the generation behind a broker instead. The turn runs as a
background task that drains the engine's generator to completion regardless of
who is listening, buffering every SSE chunk. Subscribers replay the buffer and
then follow live, so a client that arrives late — or comes back after a page
load — sees the whole turn rather than the tail of it.

Same shape as `services/run_flow.py`, for the same reason: work that outlives a
request needs an owner that isn't the request.

## Why the reply survives even with nobody watching

The engine generators persist assistant messages themselves (`add_chat_message`
inside the generator, not in the client). Draining the generator to completion
therefore writes the reply to the database whether or not a browser is
attached — so a turn finished while the user was on another page is waiting for
them in history when they come back.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, AsyncGenerator

import structlog

log = structlog.get_logger()

# How long a finished turn stays attachable. Only needs to cover the gap
# between a turn ending and a navigating client reconnecting; after that the
# reply is in the database and history is the right source.
_RETAIN_SECONDS = 120.0

# Cap on buffered chunks per turn. A long tool-calling turn emits thousands of
# small deltas; this bounds memory without truncating anything a user would
# notice, and a turn that exceeds it has already been replayed far past the
# point of usefulness.
_MAX_BUFFER = 20000


class ChatTurn:
    """One in-flight (or just-finished) assistant turn."""

    def __init__(self, conversation_id: str | None) -> None:
        self.turn_id = uuid.uuid4().hex[:12]
        self.conversation_id = conversation_id
        self.done = False
        self.started_at = time.monotonic()
        self.finished_at: float | None = None
        self._chunks: list[str] = []
        self._subscribers: list[asyncio.Queue] = []
        self._task: asyncio.Task | None = None
        self._truncated = False

    # ── plumbing ──────────────────────────────────────────────────────────

    def _emit(self, chunk: str) -> None:
        """Buffer a chunk and fan it out.

        Synchronous, with no await between the append and the fan-out: asyncio
        is single-threaded, so a subscriber can never observe a chunk in its
        queue that is missing from the buffer, or the reverse.
        """
        if len(self._chunks) < _MAX_BUFFER:
            self._chunks.append(chunk)
        elif not self._truncated:
            self._truncated = True
            log.warning("chat_turn_buffer_full", turn_id=self.turn_id)
        for queue in self._subscribers:
            queue.put_nowait(chunk)

    def _finish(self) -> None:
        self.done = True
        self.finished_at = time.monotonic()
        for queue in self._subscribers:
            queue.put_nowait(None)  # sentinel: no more chunks

    async def _drain(self, source: AsyncGenerator[str, None]) -> None:
        """Consume the engine's generator to completion, whatever the client does."""
        try:
            async for chunk in source:
                self._emit(chunk)
        except asyncio.CancelledError:
            # An explicit stop (the Stop button cancels the httpx client, which
            # ends the generator). Not an error.
            raise
        except Exception as e:  # noqa: BLE001 — a dead turn must still terminate
            log.warning("chat_turn_failed", turn_id=self.turn_id, error=str(e))
            self._emit(
                'data: {"error": "The assistant stopped unexpectedly."}\n\n'
            )
        finally:
            self._finish()
            log.info(
                "chat_turn_finished",
                turn_id=self.turn_id,
                chunks=len(self._chunks),
                seconds=round(time.monotonic() - self.started_at, 1),
            )

    # ── consumption ───────────────────────────────────────────────────────

    async def subscribe(self) -> AsyncGenerator[str, None]:
        """Replay everything so far, then follow live until the turn ends.

        A client that connects late sees the identical sequence one present
        from the start saw. That is what makes a turn reattachable: the panel
        can rebuild the whole message rather than joining mid-sentence.
        """
        queue: asyncio.Queue = asyncio.Queue()
        # Register before snapshotting the backlog, with no await between, so
        # a chunk emitted in the gap lands in the queue instead of vanishing
        # between replay and live.
        self._subscribers.append(queue)
        backlog = list(self._chunks)

        try:
            # No deduplication is needed between the two, and adding some was a
            # bug: registering and snapshotting happen with no await between
            # them, so nothing can be emitted in the gap. The backlog is
            # exactly what came before registration and the queue is exactly
            # what comes after, with no overlap.
            for chunk in backlog:
                yield chunk
            if self.done:
                return
            while True:
                chunk = await queue.get()
                if chunk is None:
                    return
                yield chunk
        finally:
            try:
                self._subscribers.remove(queue)
            except ValueError:
                pass

    def snapshot(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "active": not self.done,
        }


class ChatTurnManager:
    """Owns the current turn. One user per container, so one turn at a time."""

    def __init__(self) -> None:
        self._turn: ChatTurn | None = None

    def current(self) -> ChatTurn | None:
        turn = self._turn
        if turn is None:
            return None
        if (
            turn.done
            and turn.finished_at is not None
            and time.monotonic() - turn.finished_at > _RETAIN_SECONDS
        ):
            self._turn = None
            return None
        return turn

    def active(self) -> ChatTurn | None:
        turn = self.current()
        return turn if turn and not turn.done else None

    def start(
        self,
        source: AsyncGenerator[str, None],
        conversation_id: str | None = None,
    ) -> ChatTurn:
        """Begin draining `source` in the background and return its turn.

        Replaces any previous turn: the frontend sends one turn at a time, and
        a new POST means the user moved on from whatever came before.
        """
        previous = self.active()
        if previous is not None and previous._task is not None:
            # Don't leave an abandoned turn draining forever alongside the new
            # one — its output can no longer reach anybody.
            previous._task.cancel()

        turn = ChatTurn(conversation_id)
        self._turn = turn
        turn._task = asyncio.create_task(turn._drain(source))
        log.info("chat_turn_started", turn_id=turn.turn_id)
        return turn


manager = ChatTurnManager()
