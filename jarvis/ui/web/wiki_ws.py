"""WebSocket endpoint for the wiki live-reload stream.

Path: ``/api/wiki/live``.

Protocol
--------
- Server -> Client: one JSON message per debounced :class:`WikiPageChanged`
  event::

      {"type": "page_changed", "slug": "sam",
       "path": "entities/sam.md", "kind": "modified"}

- Client -> Server: no inbound messages are expected. The frontend uses
  this as a subscribe-only stream and re-fetches via REST after each
  message.

Per-client bounded queue
------------------------
Each connected client owns its own :class:`asyncio.Queue` with
``maxsize=64``. If the queue fills up (the client is slow to drain), new
events are **dropped** rather than back-pressuring the bus. A slow UI
must not be allowed to delay the wiki watcher or the curator that
publishes events through the same bus.

A reconnecting client will re-fetch all four REST endpoints on
``onopen``, so a missed event during a drop period is recovered by the
next fetch — there is no need to replay missed events here.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from jarvis.core.events import WikiPageChanged

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus

log = logging.getLogger(__name__)

# Per-client queue depth. 64 is enough for a curator burst (~10-15 pages
# in <300 ms) plus a couple of seconds of slow-drain margin.
QUEUE_MAXSIZE = 64

router = APIRouter(tags=["wiki-ws"])


def _resolve_bus(ws: WebSocket) -> "EventBus | None":
    """Pull the shared EventBus off ``app.state``.

    Returns ``None`` when the bus has not been wired yet (e.g. early
    startup). Callers should close the socket with code 1011 in that
    case.
    """
    app = ws.scope.get("app")
    if app is None:
        return None
    bus = getattr(app.state, "bus", None)
    return bus


@router.websocket("/api/wiki/live")
async def wiki_live(ws: WebSocket) -> None:
    """Live-reload stream for the desktop wiki view.

    Each :class:`WikiPageChanged` event is forwarded as a JSON frame.
    The endpoint does not enforce auth — the desktop app is local-only
    (AGENT-D §6 hard-negative).
    """
    await ws.accept()

    bus = _resolve_bus(ws)
    if bus is None:
        # Bus not ready yet. Close with 1011 (server error) so the
        # client retries.
        await ws.close(code=1011, reason="event bus not ready")
        return

    queue: asyncio.Queue[WikiPageChanged] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)

    async def _on_event(event: WikiPageChanged) -> None:
        """Bus subscriber: enqueue with drop-newest if the queue is full."""
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # Drop the new event. The client will re-fetch on the next
            # poll cycle / reconnect. This protects the bus from a
            # slow client.
            log.debug(
                "wiki_ws: client queue full — dropping event for %s",
                event.path,
            )

    bus.subscribe(WikiPageChanged, _on_event)

    try:
        while True:
            event = await queue.get()
            try:
                await ws.send_json(
                    {
                        "type": "page_changed",
                        "slug": event.slug,
                        "path": event.path,
                        "kind": event.kind,
                    }
                )
            except WebSocketDisconnect:
                break
            except Exception as exc:  # noqa: BLE001
                # Any send error — including the client going away
                # mid-write — terminates the stream cleanly.
                log.debug("wiki_ws: send failed (%s) — closing stream", exc)
                break
    except WebSocketDisconnect:
        pass
    finally:
        # Always remove our subscription so the bus does not grow a
        # dead reference when the client disconnects.
        try:
            bus.unsubscribe(WikiPageChanged, _on_event)
        except Exception as exc:  # noqa: BLE001
            log.debug("wiki_ws: unsubscribe raised (%s) — ignoring", exc)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["router", "wiki_live", "QUEUE_MAXSIZE"]
