"""WebSocket-Endpoint fuer den globalen Phase-6 Mission-Event-Stream.

Pfad: ``/api/missions/ws``.

Protokoll:
1. Client schickt als erstes Frame ``{"type": "hello", "last_seq": <int>,
   "token": "<str>"}``. Wenn das Frame fehlt oder fehlerhaft ist → close 4400.
   Wenn der Token ungueltig ist → close 4401.
2. Server replayt aus SQLite alle Events mit ``seq > last_seq`` in Reihenfolge
   und sendet sie unverzoeglich als JSON-Frames (ein Frame pro Envelope).
3. Server fanouted neu eintreffende Events ueber den ``MissionBus`` an alle
   verbundenen Clients (per-Client bounded queue mit drop-oldest).

Kein Heartbeat noetig — uvicorn handhabt WebSocket-Pings selbst, der Client
darf optional ``{"type": "ping"}``-Frames schicken; sie werden ignoriert.

Drop-Oldest-Begruendung: ein langsamer/blockierter Client darf den Bus nicht
bremsen — der Voice-Pfad teilt sich mit dem WS-Pfad keine Critical-Path-
Latenz, aber die Sub-Mission-Tasks emittieren u.U. burstartig (Worker-Spawn
+ Worker-Progress + Critic-Verdict auf einmal). Fenstergroesse 200 reicht
fuer ein paar Sekunden Backlog vor dem ersten Drop.
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


class ConnectionManager:
    """Per-Client bounded ``asyncio.Queue`` + globaler Fanout.

    Eine Instanz pro Server. Wird beim Server-Start in ``app.state.missions_ws_manager``
    abgelegt und an ``MissionBus.subscribe_all()`` gehaengt — so landet jedes
    persistierte Event automatisch in jeder Client-Queue.
    """

    def __init__(self) -> None:
        self._clients: dict[str, asyncio.Queue[EventEnvelope]] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        client_id: str,
        last_seq: int,
        store: "MissionEventStore",
    ) -> asyncio.Queue[EventEnvelope]:
        """Registriert einen Client und enqueued alle Replay-Events ab ``last_seq``.

        Replay laeuft synchron vor Registrierung — so verpasst der Client
        keine Events, die zwischen ``events_since()`` und ``subscribe_all()``
        publiziert wuerden.
        """
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(
            maxsize=_QUEUE_MAXSIZE
        )
        # Replay erst, dann registrieren — sonst racet Live-Fanout mit Replay.
        replay = await store.events_since(last_seq)
        for env in replay:
            try:
                queue.put_nowait(env)
            except asyncio.QueueFull:
                # Replay-Burst groesser als Queue — die aeltesten Replay-
                # Events haben den Vortritt vor Live-Events.
                try:
                    queue.get_nowait()
                    queue.put_nowait(env)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
        async with self._lock:
            self._clients[client_id] = queue
        return queue

    async def disconnect(self, client_id: str) -> None:
        async with self._lock:
            self._clients.pop(client_id, None)

    async def fanout(self, env: EventEnvelope) -> None:
        """``MissionBus.subscribe_all``-Handler. Drop-Oldest pro Client."""
        # Snapshot ohne Lock — Modifikationen am Dict sind asyncio-thread-safe
        # (eine Loop pro Server), wir lesen nur Werte.
        for client_id, queue in list(self._clients.items()):
            try:
                queue.put_nowait(env)
            except asyncio.QueueFull:
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

    @property
    def client_count(self) -> int:
        return len(self._clients)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


def _resolve_manager(ws: WebSocket) -> tuple[ConnectionManager, "MissionManager"] | None:
    """Sucht ``missions_ws_manager`` + ``mission_manager`` in app.state."""
    app = ws.scope["app"]
    mgr = getattr(app.state, "missions_ws_manager", None)
    mission_manager = getattr(app.state, "mission_manager", None)
    if mgr is None or mission_manager is None:
        return None
    return mgr, mission_manager


@router.websocket("/ws")
async def missions_ws(ws: WebSocket) -> None:
    """Globaler Mission-Event-Stream (Hello → Replay → Live)."""
    await ws.accept()

    resolved = _resolve_manager(ws)
    if resolved is None:
        await ws.close(code=1011, reason="mission stack not initialised")
        return
    conn_mgr, mission_manager = resolved

    # 1. Hello-Frame (Timeout 5s).
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

    # 2. Reader-Task (drained Client-Frames ohne sie zu interpretieren).
    async def _reader() -> None:
        while True:
            try:
                msg = await ws.receive_json()
            except WebSocketDisconnect:
                return
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "missions_ws: client frame decode error (%s) — ignoring",
                    exc,
                )
                continue
            # Reserved fuer Future-Control-Frames (pause/resume etc.).
            if isinstance(msg, dict) and msg.get("type") == "ping":
                # Schweigender Pong-Skip — Bus-Pings reichen.
                continue

    reader_task = asyncio.create_task(
        _reader(), name=f"missions_ws-reader-{client_id[:8]}"
    )

    # 3. Writer-Loop. Bei WS-Disconnect: stoppen.
    try:
        while True:
            env = await queue.get()
            try:
                await ws.send_json(env.model_dump(mode="json"))
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
