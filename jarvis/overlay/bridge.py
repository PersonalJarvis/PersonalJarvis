"""``OverlayBridge`` — Hauptjarvis-seitige IPC-Faceade.

Phase 9.2:
- ``send`` / ``send_nowait`` mit bounded ``asyncio.Queue(maxsize=256)``.
- Drop-Policy nach Plan §10.4: drop oldest non-state first, drop state last.
- WS-Handler-Coroutine fuer ``websockets.serve(...)``. Validiert eingehende
  Messages via ``IPCMessage.validate_json``.
- Aktuellen State cachen (``last_state_envelope``) damit ein Reconnect den
  Resync-Frame als erstes bekommt (§10.5).

Noch NICHT in 9.2: Coalescing (AD-17), Cursor-SHM, State-Machine. Phase 9.3
liefert die State-Machine ueber dieser Bridge.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

import os

from jarvis.overlay.schema import (
    ActionEndedEnvelope,
    ActionEndedPayload,
    ActionStartedEnvelope,
    ActionStartedPayload,
    ClickEnvelope,
    ClickPayload,
    ErrorEnvelope,
    ErrorPayload,
    HeartbeatEnvelope,
    HeartbeatPayload,
    IPCMessage,
    StateEnvelope,
    StatePayload,
    is_state_type,
    new_ulid,
    now_ns,
)

logger = logging.getLogger(__name__)

OUTBOUND_QUEUE_MAX = 256
INBOUND_QUEUE_MAX = 64

# Mock-Type fuer ``websockets.WebSocketServerProtocol``. Wir typen lose,
# damit der Modul-Import nicht von ``websockets`` abhaengt (Headless-Tests).
WSConn = Any


class _BoundedOutbox:
    """Bounded Outbound-Queue mit Drop-Policy (§10.4).

    Implementiert als ``deque(maxlen=N)`` plus ``asyncio.Event`` fuer
    Wake-Up. Wir benutzen kein ``asyncio.Queue.put_nowait``, weil dessen
    Full-Behavior ``QueueFull``-Exception ist; wir wollen Drop-Oldest-Non-
    State, also brauchen wir handcrafted Drop-Logic.
    """

    def __init__(self, maxsize: int = OUTBOUND_QUEUE_MAX) -> None:
        self._max = maxsize
        # ``deque`` ohne maxlen — Eviction machen wir selbst.
        self._buf: deque[bytes] = deque()
        self._types: deque[str] = deque()  # parallel zur _buf
        self._wake = asyncio.Event()
        self._dropped = 0

    @property
    def dropped(self) -> int:
        return self._dropped

    def __len__(self) -> int:
        return len(self._buf)

    def put(self, envelope_type: str, raw_json: bytes) -> bool:
        """Non-blocking enqueue. Returnt ``True`` bei Aufnahme.

        Drop-Policy: Wenn voll, droppe das aelteste *Non-State*-Element.
        Wenn nur State-Elemente in der Queue sind, droppe nichts und
        verwerfe stattdessen den NEUEN Non-State-Eintrag (state wird nie
        gedroppt).
        """
        if len(self._buf) < self._max:
            self._buf.append(raw_json)
            self._types.append(envelope_type)
            self._wake.set()
            return True

        # Voll. Suche aelteste Non-State.
        for idx, t in enumerate(self._types):
            if not is_state_type(t):
                # Diesen Eintrag rauswerfen.
                del self._buf[idx]
                del self._types[idx]
                self._buf.append(raw_json)
                self._types.append(envelope_type)
                self._dropped += 1
                self._wake.set()
                return True

        # Queue komplett State. Dann darf nur ein State-Element rein.
        if is_state_type(envelope_type):
            # Aeltesten State droppen.
            self._buf.popleft()
            self._types.popleft()
            self._buf.append(raw_json)
            self._types.append(envelope_type)
            self._dropped += 1
            self._wake.set()
            return True

        # Non-State + nur State in Queue -> neuen droppen.
        self._dropped += 1
        return False

    async def get(self) -> bytes:
        """Async pop. Blockiert bis ein Element verfuegbar ist."""
        while not self._buf:
            self._wake.clear()
            await self._wake.wait()
        item = self._buf.popleft()
        self._types.popleft()
        return item

    def clear(self) -> None:
        self._buf.clear()
        self._types.clear()
        self._wake.clear()


class OverlayBridge:
    """Hauptjarvis-seitige WS-Server-Bridge.

    Lifecycle:
        bridge = OverlayBridge()
        await bridge.start()
        # ... bridge.emit_state(...) etc ...
        await bridge.stop()

    WS-Handler:
        await websockets.serve(bridge.handler, "127.0.0.1", port)
    """

    def __init__(
        self,
        *,
        outbound_queue_max: int = OUTBOUND_QUEUE_MAX,
        heartbeat_interval_s: float = 1.0,
    ) -> None:
        self._outbox = _BoundedOutbox(maxsize=outbound_queue_max)
        self._heartbeat_interval = heartbeat_interval_s
        self._started_at_ns: int = 0
        self._connected: set[WSConn] = set()
        self._last_state_envelope: Optional[StateEnvelope] = None
        self._inbound_handlers: list[Callable[[Any], Awaitable[None]]] = []
        self._tasks: list[asyncio.Task[Any]] = []
        self._running = False

    # --- lifecycle ---

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._started_at_ns = now_ns()
        # Heartbeat-Pump alle ``_heartbeat_interval`` Sekunden.
        self._tasks.append(
            asyncio.create_task(self._heartbeat_loop(), name="overlay-heartbeat")
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()
        self._outbox.clear()

    # --- public emit-API (sync, queue-only) ---

    def send_nowait(self, envelope: Any) -> bool:
        """Sync-API. Serialisiert das Envelope und pusht in die Outbox."""
        raw = envelope.model_dump_json().encode("utf-8")
        if isinstance(envelope, StateEnvelope):
            self._last_state_envelope = envelope
        return self._outbox.put(envelope.type, raw)

    async def send(self, envelope: Any) -> bool:
        """Async-API. Aktuell identisch zu ``send_nowait`` weil die Queue
        non-blocking ist; signature bleibt async fuer 9.3+ Erweiterungen."""
        return self.send_nowait(envelope)

    def emit_state(
        self,
        state: str,
        *,
        intensity: float = 1.0,
        reason: Optional[str] = None,
    ) -> bool:
        env = StateEnvelope(
            payload=StatePayload(
                state=state,  # type: ignore[arg-type]
                intensity=intensity,
                reason=reason,  # type: ignore[arg-type]
            )
        )
        return self.send_nowait(env)

    def emit_action_started(
        self,
        kind: str,
        *,
        duration_hint_ms: Optional[int] = None,
        action_id: Optional[str] = None,
    ) -> str:
        """Plan §8.4 — vor Function-Call. Returnt die action_id damit
        emit_action_ended sie referenzieren kann."""
        aid = action_id or new_ulid()
        env = ActionStartedEnvelope(
            payload=ActionStartedPayload(
                kind=kind,  # type: ignore[arg-type]
                action_id=aid,
                duration_hint_ms=duration_hint_ms,
            )
        )
        self.send_nowait(env)
        return aid

    def emit_action_ended(
        self,
        action_id: str,
        *,
        succeeded: bool = True,
        duration_actual_ms: Optional[int] = None,
    ) -> bool:
        """Plan §8.4 — im finally-Block (auch bei Exception)."""
        env = ActionEndedEnvelope(
            payload=ActionEndedPayload(
                action_id=action_id,
                succeeded=succeeded,
                duration_actual_ms=duration_actual_ms,
            )
        )
        return self.send_nowait(env)

    def emit_click(
        self,
        x: int,
        y: int,
        *,
        monitor: str = "",
        button: str = "left",
        modifiers: Optional[list[str]] = None,
    ) -> bool:
        """Plan §14.3 — emit BEFORE pyautogui.click() so Visual reicht
        WebView before OS Click Event finished propagating."""
        env = ClickEnvelope(
            payload=ClickPayload(
                x=int(x),
                y=int(y),
                monitor=monitor,
                button=button,  # type: ignore[arg-type]
                modifiers=list(modifiers) if modifiers else [],
            )
        )
        return self.send_nowait(env)

    def emit_error(
        self,
        message: str,
        *,
        code: str = "OVERLAY_ACTION_ERROR",
        recoverable: bool = True,
        context: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Plan §8.4 — bei Exception in @overlay_action."""
        env = ErrorEnvelope(
            payload=ErrorPayload(
                code=code,
                message=message,
                recoverable=recoverable,
                context=dict(context) if context else {},
            )
        )
        return self.send_nowait(env)

    def add_inbound_handler(self, handler: Callable[[Any], Awaitable[None]]) -> None:
        self._inbound_handlers.append(handler)

    # --- queue access ---

    @property
    def outbox(self) -> _BoundedOutbox:
        return self._outbox

    @property
    def last_state_envelope(self) -> Optional[StateEnvelope]:
        return self._last_state_envelope

    @property
    def connected_count(self) -> int:
        return len(self._connected)

    # --- WS handler ---

    async def handler(self, websocket: WSConn) -> None:
        """``websockets.serve(...)``-kompatibler Handler.

        State-Resync per §10.5: erstes Frame nach Connect ist der zuletzt
        bekannte State (falls vorhanden).
        """
        self._connected.add(websocket)
        try:
            if self._last_state_envelope is not None:
                resync = self._last_state_envelope.model_dump_json().encode("utf-8")
                await websocket.send(resync)

            sender = asyncio.create_task(
                self._send_loop(websocket), name="overlay-ws-send"
            )
            recver = asyncio.create_task(
                self._recv_loop(websocket), name="overlay-ws-recv"
            )
            done, pending = await asyncio.wait(
                {sender, recver}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception()
                if exc is not None and not isinstance(exc, asyncio.CancelledError):
                    logger.warning("overlay WS task died: %r", exc)
        finally:
            self._connected.discard(websocket)

    async def _send_loop(self, websocket: WSConn) -> None:
        while True:
            raw = await self._outbox.get()
            await websocket.send(raw)

    async def _recv_loop(self, websocket: WSConn) -> None:
        async for raw in websocket:
            try:
                msg = IPCMessage.validate_json(raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("overlay WS validate failed: %r raw=%r", exc, raw[:200])
                continue
            for h in self._inbound_handlers:
                try:
                    await h(msg)
                except Exception:  # noqa: BLE001
                    logger.exception("inbound handler raised")

    # --- internals ---

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                env = HeartbeatEnvelope(
                    payload=HeartbeatPayload(
                        uptime_s=(now_ns() - self._started_at_ns) / 1e9,
                        ws_connected=bool(self._connected),
                    )
                )
                self.send_nowait(env)
            except Exception:  # noqa: BLE001
                logger.exception("heartbeat emit failed")
            try:
                await asyncio.sleep(self._heartbeat_interval)
            except asyncio.CancelledError:
                return


def envelope_to_json(envelope: Any) -> bytes:
    """Helper fuer Tests."""
    return envelope.model_dump_json().encode("utf-8")


# -------------------------------------------------------------------------
# Sub-Agent No-Op-Stub. Plan §8.7 / AD-6.
# -------------------------------------------------------------------------


JARVIS_DEPTH_ENV: str = "JARVIS_DEPTH"


def is_sub_agent_process() -> bool:
    """True wenn ``JARVIS_DEPTH > 0`` env. Sub-Agent-Code laeuft mit
    Depth >= 1 (Phase-5-Convention)."""
    raw = os.environ.get(JARVIS_DEPTH_ENV, "0")
    try:
        return int(raw) > 0
    except (ValueError, TypeError):
        return False


class NoOpOverlayBridge:
    """No-Op-Stub fuer Sub-Agents. Plan §8.7 / AD-6.

    Hat dieselbe Public-API wie ``OverlayBridge``, aber alle Methoden
    sind Lifeless-Stubs. Sub-Agent-Code kann daher dasselbe Pattern
    schreiben (``bridge.emit_click(...)``) ohne Special-Casing — die
    Events landen einfach im Void.

    Wichtig: Wir vererben NICHT von OverlayBridge weil dessen __init__
    eine asyncio.Queue baut und in Sub-Agent-Sync-Pfaden gibt es ggf.
    keinen Event-Loop. Stattdessen duck-typed Stub.
    """

    @property
    def dropped(self) -> int:
        return 0

    @property
    def connected_count(self) -> int:
        return 0

    @property
    def last_state_envelope(self) -> Optional[StateEnvelope]:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def send_nowait(self, _envelope: Any) -> bool:
        return False

    async def send(self, _envelope: Any) -> bool:
        return False

    def emit_state(
        self,
        _state: str,
        *,
        intensity: float = 1.0,  # noqa: ARG002 — API-Symmetrie
        reason: Optional[str] = None,  # noqa: ARG002
    ) -> bool:
        return False

    def emit_action_started(
        self,
        _kind: str,
        *,
        duration_hint_ms: Optional[int] = None,  # noqa: ARG002
        action_id: Optional[str] = None,
    ) -> str:
        # Stub returnt eine action_id damit Caller-Code weiterlaufen kann.
        return action_id or "noop"

    def emit_action_ended(
        self,
        _action_id: str,
        *,
        succeeded: bool = True,  # noqa: ARG002
        duration_actual_ms: Optional[int] = None,  # noqa: ARG002
    ) -> bool:
        return False

    def emit_click(
        self,
        _x: int,
        _y: int,
        *,
        monitor: str = "",  # noqa: ARG002
        button: str = "left",  # noqa: ARG002
        modifiers: Optional[list[str]] = None,  # noqa: ARG002
    ) -> bool:
        return False

    def emit_error(
        self,
        _message: str,
        *,
        code: str = "OVERLAY_ACTION_ERROR",  # noqa: ARG002
        recoverable: bool = True,  # noqa: ARG002
        context: Optional[dict[str, Any]] = None,  # noqa: ARG002
    ) -> bool:
        return False

    def add_inbound_handler(
        self, _handler: Callable[[Any], Awaitable[None]]
    ) -> None:
        return None
