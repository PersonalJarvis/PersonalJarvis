"""WebSocket endpoint for the global Phase-6 mission event stream.

Path: ``/api/missions/ws``.

Protocol:
1. The client sends ``{"type": "hello", "last_seq": <int>,
   "token": "<str>"}`` as the first frame. If the frame is missing or malformed → close 4400.
   If the token is invalid → close 4401.
2. The server replays all events with ``seq > last_seq`` from SQLite in order
   and sends them immediately as JSON frames (one frame per envelope).
3. The server fans newly arriving events out over the ``MissionBus`` to all
   connected clients (per-client bounded queue with drop-oldest).

No heartbeat needed — uvicorn handles WebSocket pings itself; the client
may optionally send ``{"type": "ping"}`` frames, which are ignored.

Drop-oldest rationale: a slow/blocked client must not throttle the bus —
the voice path doesn't share critical-path latency with the WS path, but
sub-mission tasks may emit bursts (worker spawn + worker progress +
critic verdict all at once). A window size of 200 covers a few seconds of
backlog before the first drop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from jarvis.missions.events import EventEnvelope

if TYPE_CHECKING:
    from jarvis.missions.event_store import MissionEventStore
    from jarvis.missions.manager import MissionManager

from .missions_auth import validate_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/missions", tags=["missions-ws"])


# ---------------------------------------------------------------------------
# ConnectionManager
# ---------------------------------------------------------------------------


_QUEUE_MAXSIZE = 200
_HELLO_TIMEOUT_S = 5.0

# Sent at most once per connection when a drop-oldest overflow happens, so
# the client knows its stream has a gap and should resync via REST.
_GAP_FRAME: dict[str, Any] = {"type": "gap"}


class ConnectionManager:
    """Per-client bounded ``asyncio.Queue`` + global fanout.

    One instance per server. Stored in ``app.state.missions_ws_manager``
    at server start and attached to ``MissionBus.subscribe_all()`` — so every
    persisted event automatically lands in every client queue.
    """

    def __init__(self) -> None:
        self._clients: dict[str, asyncio.Queue[EventEnvelope | dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self._gap_notified: set[str] = set()

    async def connect(
        self,
        client_id: str,
        last_seq: int,
        store: "MissionEventStore",
    ) -> asyncio.Queue[EventEnvelope | dict[str, Any]]:
        """Registers a client, then enqueues all replay events since ``last_seq``.

        Registration happens BEFORE the replay query so that any event
        ``fanout()`` publishes while the query is in flight lands in the
        queue instead of being lost in the gap between ``events_since()``
        and registration. Once the query returns we drain whatever landed
        there in the meantime (no ``await`` in between — single-threaded
        event loop, so nothing else can touch the queue during the drain)
        and merge it after the replay, skipping anything the replay already
        covers so the client never sees a duplicate.
        """
        queue: asyncio.Queue[EventEnvelope | dict[str, Any]] = asyncio.Queue(
            maxsize=_QUEUE_MAXSIZE
        )
        async with self._lock:
            self._clients[client_id] = queue

        replay = await store.events_since(last_seq)

        live_during_replay: list[EventEnvelope] = []
        while not queue.empty():
            item = queue.get_nowait()
            if isinstance(item, EventEnvelope):
                live_during_replay.append(item)

        max_replayed_seq = (
            replay[-1].seq
            if replay and replay[-1].seq is not None
            else last_seq
        )
        merged = list(replay) + [
            env
            for env in live_during_replay
            if env.seq is not None and env.seq > max_replayed_seq
        ]
        for env in merged:
            self._enqueue(client_id, queue, env)
        return queue

    async def disconnect(self, client_id: str) -> None:
        async with self._lock:
            self._clients.pop(client_id, None)
        self._gap_notified.discard(client_id)

    def _enqueue(
        self,
        client_id: str,
        queue: asyncio.Queue[EventEnvelope | dict[str, Any]],
        env: EventEnvelope,
    ) -> None:
        """``put_nowait`` with drop-oldest; on drop, arm a one-time gap notice."""
        try:
            queue.put_nowait(env)
            return
        except asyncio.QueueFull:
            pass
        try:
            queue.get_nowait()
            queue.put_nowait(env)
        except asyncio.QueueEmpty:
            pass
        except asyncio.QueueFull:
            logger.warning(
                "missions_ws: client=%s queue still full after drop",
                client_id,
            )
        self._notify_gap(client_id, queue)

    def _notify_gap(
        self,
        client_id: str,
        queue: asyncio.Queue[EventEnvelope | dict[str, Any]],
    ) -> None:
        """Enqueues one ``{"type": "gap"}`` frame per client so it resyncs via REST."""
        if client_id in self._gap_notified:
            return
        self._gap_notified.add(client_id)
        try:
            queue.put_nowait(_GAP_FRAME)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(_GAP_FRAME)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def fanout(self, env: EventEnvelope) -> None:
        """``MissionBus.subscribe_all`` handler. Drop-oldest per client."""
        # Snapshot without a lock — modifications to the dict are asyncio-thread-safe
        # (one loop per server), and we're only reading values.
        for client_id, queue in list(self._clients.items()):
            self._enqueue(client_id, queue, env)

    @property
    def client_count(self) -> int:
        return len(self._clients)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


def _resolve_manager(ws: WebSocket) -> tuple[ConnectionManager, "MissionManager"] | None:
    """Looks up ``missions_ws_manager`` + ``mission_manager`` in app.state."""
    app = ws.scope["app"]
    mgr = getattr(app.state, "missions_ws_manager", None)
    mission_manager = getattr(app.state, "mission_manager", None)
    if mgr is None or mission_manager is None:
        return None
    return mgr, mission_manager


@router.websocket("/ws")
async def missions_ws(ws: WebSocket) -> None:
    """Global mission event stream (hello → replay → live)."""
    await ws.accept()

    resolved = _resolve_manager(ws)
    if resolved is None:
        await ws.close(code=1011, reason="mission stack not initialised")
        return
    conn_mgr, mission_manager = resolved

    # 1. Hello frame (5s timeout).
    try:
        first = await asyncio.wait_for(
            ws.receive_json(), timeout=_HELLO_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        await ws.close(code=4400, reason="hello timeout")
        return
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("missions_ws: hello-decode failed: %s", exc)
        await ws.close(code=4400, reason="hello decode error")
        return

    if not isinstance(first, dict) or first.get("type") != "hello":
        await ws.close(code=4400, reason="expected hello frame first")
        return

    token = str(first.get("token", ""))
    if not validate_token(token):
        await ws.close(code=4401, reason="unauthorized")
        return

    try:
        last_seq = int(first.get("last_seq", 0))
    except (TypeError, ValueError):
        await ws.close(code=4400, reason="last_seq must be int")
        return

    client_id = uuid4().hex
    queue = await conn_mgr.connect(client_id, last_seq, mission_manager.store)

    # 2. Reader task (drains client frames without interpreting them).
    async def _reader() -> None:
        while True:
            try:
                msg = await ws.receive_json()
            except WebSocketDisconnect:
                return
            except RuntimeError as exc:
                # AP-20: an unclean client disconnect raises RuntimeError
                # ("WebSocket is not connected ...") instead of
                # WebSocketDisconnect. `continue` here would re-poll the dead
                # socket forever — treat it as terminal.
                logger.debug(
                    "missions_ws: reader socket error (%s) — closing", exc
                )
                return
            except ValueError as exc:
                # Malformed JSON (json.JSONDecodeError is a ValueError) on an
                # otherwise-live socket — skip the bad frame, keep reading.
                logger.debug(
                    "missions_ws: client frame decode error (%s) — ignoring",
                    exc,
                )
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "missions_ws: reader unexpected error (%s) — closing", exc
                )
                return
            # Reserved for future control frames (pause/resume etc.).
            if isinstance(msg, dict) and msg.get("type") == "ping":
                # Silent pong skip — bus pings are enough.
                continue

    reader_task = asyncio.create_task(
        _reader(), name=f"missions_ws-reader-{client_id[:8]}"
    )

    # 3. Writer loop. Stop on WS disconnect.
    try:
        while True:
            env = await queue.get()
            frame = env.model_dump(mode="json") if isinstance(env, EventEnvelope) else env
            try:
                await ws.send_json(frame)
            except WebSocketDisconnect:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "missions_ws: send failed for client=%s: %s",
                    client_id,
                    exc,
                )
                break
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await conn_mgr.disconnect(client_id)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["ConnectionManager", "router"]
