"""SSE transport layer — SSEHub + FastAPI router.

Bridges the in-process EventBus to network clients via Server-Sent Events.
Every connected client opens `GET /api/stream?session_id=<id>` and receives
a stream of typed SSE events routed by session_id.

Event mapping (bus → SSE wire):
    AckEmitted          → event "ack",            data {"text": ev.text}
    WorkerStarted       → event "worker_started", data {"mission_id": ev.mission_id}
    WorkerCompleted     → event "answer",         data {"text": ev.result,
                                                         "mission_id": ev.mission_id}
    WorkerCorrectionNeeded → NOT streamed (invisible; pushed explicitly via hub.push
                             after the VAD turn boundary — AD-OE5).
    All other events       → ignored.

Explicit push:
    hub.push(session_id, event_name, data_dict) enqueues an arbitrary SSE
    message for every client currently listening on that session. Used by the
    orchestrator to deliver flushed Oops corrections.

Concurrency model:
    - Each client call to hub.stream() allocates a fresh asyncio.Queue and
      registers it under its session_id.
    - _on_event (called by the bus) fans out to all queues for the target
      session_id.
    - The EventSourceResponse generator pulls from the queue with a short
      timeout loop so that asyncio task cancellation (client disconnect) is
      handled cleanly without leaking the queue.
    - The queue is unregistered in a finally block on disconnect.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from optimistic.events import (
    AckEmitted,
    Event,
    WorkerCompleted,
    WorkerStarted,
)

_log = logging.getLogger("optimistic.sse")

# How long to block on queue.get() before looping back.
# Short enough not to delay disconnect detection; long enough not to spin-burn.
_QUEUE_POLL_TIMEOUT: float = 0.5

# Sentinel placed in the queue to signal the generator to stop.
_STOP = object()


class SSEHub:
    """Fan-out hub: subscribes to the EventBus and delivers per-session SSE
    messages to every connected client queue for that session."""

    def __init__(self, bus) -> None:
        # {session_id: set[asyncio.Queue]}
        self._queues: dict[str, set[asyncio.Queue]] = {}
        bus.subscribe_all(self._on_event)

    # ------------------------------------------------------------------
    # Bus → SSE mapping
    # ------------------------------------------------------------------

    async def _on_event(self, ev: Event) -> None:
        """Translate bus events to SSE messages and enqueue to all queues
        registered for ev.session_id. Silently ignores unmapped event types."""
        if isinstance(ev, AckEmitted):
            await self.push(ev.session_id, "ack", {"text": ev.text})
        elif isinstance(ev, WorkerStarted):
            await self.push(
                ev.session_id, "worker_started", {"mission_id": ev.mission_id}
            )
        elif isinstance(ev, WorkerCompleted):
            await self.push(
                ev.session_id,
                "answer",
                {"text": ev.result, "mission_id": ev.mission_id},
            )
        # WorkerCorrectionNeeded: intentionally NOT streamed here.
        # It is invisible on the SSE stream until the VAD turn boundary,
        # at which point the orchestrator calls hub.push("...", "correction", {...}).

    # ------------------------------------------------------------------
    # Explicit push API
    # ------------------------------------------------------------------

    async def push(
        self, session_id: str, event_name: str, data: dict
    ) -> None:
        """Enqueue an SSE message for every client listening on session_id.

        The enqueued dict is shaped for sse_starlette's EventSourceResponse:
            {"event": <name>, "data": <json string>}
        """
        message = {"event": event_name, "data": json.dumps(data)}
        queues = self._queues.get(session_id)
        if not queues:
            return
        for q in set(queues):  # snapshot to avoid mutation during iteration
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                _log.warning(
                    "SSEHub: queue full for session %r, dropping event %r",
                    session_id,
                    event_name,
                )

    # ------------------------------------------------------------------
    # Stream factory
    # ------------------------------------------------------------------

    def stream(self, session_id: str) -> EventSourceResponse:
        """Register a fresh asyncio.Queue for this session and return an
        EventSourceResponse whose generator yields queued SSE messages.

        The queue is unregistered in a finally block so no leak occurs on
        client disconnect or generator cancellation.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._queues.setdefault(session_id, set()).add(q)
        _log.debug("SSEHub: new client connected to session %r", session_id)

        async def _generator() -> AsyncGenerator[dict, None]:
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(
                            q.get(), timeout=_QUEUE_POLL_TIMEOUT
                        )
                    except TimeoutError:
                        # No message yet — loop back. This is how disconnect
                        # is detected: the generator is cancelled by the ASGI
                        # framework when the client disconnects, so the
                        # CancelledError propagates to the finally block below.
                        continue
                    if msg is _STOP:
                        return
                    yield msg
            finally:
                # Unregister this queue — must happen even on cancellation.
                queues = self._queues.get(session_id)
                if queues:
                    queues.discard(q)
                    if not queues:
                        self._queues.pop(session_id, None)
                _log.debug(
                    "SSEHub: client disconnected from session %r", session_id
                )

        # ping=60: send a keepalive comment every 60 s.
        # Tests use the raw ASGI interface so pings are not an issue there.
        return EventSourceResponse(_generator(), ping=60)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def build_sse_router(hub: SSEHub) -> APIRouter:
    """Return an APIRouter with a single GET /api/stream?session_id= endpoint."""
    router = APIRouter()

    @router.get("/api/stream")
    async def stream(session_id: str = "default") -> EventSourceResponse:
        """Open an SSE stream for the given session.  Stays open until the
        client disconnects.  Delivers ack, worker_started, answer, and any
        explicit correction events for this session_id."""
        return hub.stream(session_id)

    return router
