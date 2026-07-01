"""``OverlayBridge`` — main-Jarvis-side IPC facade.

Phase 9.2:
- ``send`` / ``send_nowait`` with a bounded ``asyncio.Queue(maxsize=256)``.
- Drop policy per Plan §10.4: drop oldest non-state first, drop state last.
- WS handler coroutine for ``websockets.serve(...)``. Validates incoming
  messages via ``IPCMessage.validate_json``.
- Caches the current state (``last_state_envelope``) so a reconnect gets
  the resync frame first (§10.5).

NOT YET in 9.2: coalescing (AD-17), cursor SHM, state machine. Phase 9.3
delivers the state machine on top of this bridge.
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

# Mock type for ``websockets.WebSocketServerProtocol``. We type loosely,
# so the module import doesn't depend on ``websockets`` (headless tests).
WSConn = Any


class _BoundedOutbox:
    """Bounded outbound queue with drop policy (§10.4).

    Implemented as a ``deque(maxlen=N)`` plus an ``asyncio.Event`` for
    wake-up. We don't use ``asyncio.Queue.put_nowait`` because its
    full-behavior is a ``QueueFull`` exception; we want drop-oldest-non-
    state, so we need handcrafted drop logic.
    """

    def __init__(self, maxsize: int = OUTBOUND_QUEUE_MAX) -> None:
        self._max = maxsize
        # ``deque`` without maxlen — we handle eviction ourselves.
        self._buf: deque[bytes] = deque()
        self._types: deque[str] = deque()  # parallel to _buf
        self._wake = asyncio.Event()
        self._dropped = 0

    @property
    def dropped(self) -> int:
        return self._dropped

    def __len__(self) -> int:
        return len(self._buf)

    def put(self, envelope_type: str, raw_json: bytes) -> bool:
        """Non-blocking enqueue. Returns ``True`` on acceptance.

        Drop policy: if full, drop the oldest *non-state* element.
        If only state elements are in the queue, drop nothing and
        instead discard the NEW non-state entry (state is never
        dropped).
        """
        if len(self._buf) < self._max:
            self._buf.append(raw_json)
            self._types.append(envelope_type)
            self._wake.set()
            return True

        # Full. Look for the oldest non-state.
        for idx, t in enumerate(self._types):
            if not is_state_type(t):
                # Evict this entry.
                del self._buf[idx]
                del self._types[idx]
                self._buf.append(raw_json)
                self._types.append(envelope_type)
                self._dropped += 1
                self._wake.set()
                return True

        # Queue is entirely state. Then only one state element may go in.
        if is_state_type(envelope_type):
            # Drop the oldest state.
            self._buf.popleft()
            self._types.popleft()
            self._buf.append(raw_json)
            self._types.append(envelope_type)
            self._dropped += 1
            self._wake.set()
            return True

        # Non-state + only state in queue -> drop the new one.
        self._dropped += 1
        return False

    async def get(self) -> bytes:
        """Async pop. Blocks until an element is available."""
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
    """Main-Jarvis-side WS server bridge.

    Lifecycle:
        bridge = OverlayBridge()
        await bridge.start()
        # ... bridge.emit_state(...) etc ...
        await bridge.stop()

    WS handler:
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
        # Heartbeat pump every ``_heartbeat_interval`` seconds.
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
        """Sync API. Serializes the envelope and pushes it into the outbox."""
        raw = envelope.model_dump_json().encode("utf-8")
        if isinstance(envelope, StateEnvelope):
            self._last_state_envelope = envelope
        return self._outbox.put(envelope.type, raw)

    async def send(self, envelope: Any) -> bool:
        """Async API. Currently identical to ``send_nowait`` because the
        queue is non-blocking; the signature stays async for 9.3+ extensions."""
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
        """Plan §8.4 — before the function call. Returns the action_id so
        emit_action_ended can reference it."""
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
        """Plan §8.4 — in the finally block (also on exception)."""
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
        """Plan §14.3 — emit BEFORE pyautogui.click() so Visual reaches
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
        """Plan §8.4 — on exception in @overlay_action."""
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
        """``websockets.serve(...)``-compatible handler.

        State resync per §10.5: the first frame after connect is the last
        known state (if any).
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
    """Helper for tests."""
    return envelope.model_dump_json().encode("utf-8")


# -------------------------------------------------------------------------
# Sub-agent no-op stub. Plan §8.7 / AD-6.
# -------------------------------------------------------------------------


JARVIS_DEPTH_ENV: str = "JARVIS_DEPTH"


def is_sub_agent_process() -> bool:
    """True when the ``JARVIS_DEPTH > 0`` env is set. Sub-agent code runs with
    depth >= 1 (Phase-5 convention)."""
    raw = os.environ.get(JARVIS_DEPTH_ENV, "0")
    try:
        return int(raw) > 0
    except (ValueError, TypeError):
        return False


class NoOpOverlayBridge:
    """No-op stub for sub-agents. Plan §8.7 / AD-6.

    Has the same public API as ``OverlayBridge``, but all methods
    are lifeless stubs. Sub-agent code can therefore write the same
    pattern (``bridge.emit_click(...)``) without special-casing — the
    events simply vanish into the void.

    Important: we do NOT inherit from OverlayBridge because its __init__
    builds an asyncio.Queue, and sub-agent sync paths may have no
    event loop. Instead: a duck-typed stub.
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
        intensity: float = 1.0,  # noqa: ARG002 — API symmetry
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
        # Stub returns an action_id so caller code can keep going.
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
