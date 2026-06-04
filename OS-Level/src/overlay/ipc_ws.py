"""WS-Client. Verbindet zum Hauptjarvis-WS-Server.

Plan §10.5 — Lifecycle:
- Initial-Connect: probiert Ports ``[ws_port..ws_port_range_max]`` durch.
- Heartbeat: client sendet alle 1s, erwartet ebenfalls 1s-Frequenz vom
  Server. Wenn 3s lang KEIN Frame ankommt -> Connection als broken markieren.
- Reconnect-Backoff: 0.5, 1, 2, 4, 8, 30 Sekunden mit +/- 20% Jitter.
- State-Resync: erstes Frame vom Server nach Connect ist der aktuelle
  State; wir reichen ihn unaendert an ``on_message`` durch.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, Optional, Sequence

import websockets
from websockets.asyncio.client import connect

from .schema import (
    HeartbeatEnvelope,
    HeartbeatPayload,
    IPCMessage,
    SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)

# Plan §10.5 — fixed backoff slots in Sekunden.
BACKOFF_SCHEDULE: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 8.0, 30.0)
JITTER_FRACTION = 0.2

OnMessage = Callable[[object], Awaitable[None]]


def _backoff_with_jitter(slot: int, *, rng: random.Random | None = None) -> float:
    rng = rng or random
    base = BACKOFF_SCHEDULE[min(slot, len(BACKOFF_SCHEDULE) - 1)]
    jitter = base * JITTER_FRACTION
    return max(0.05, base + rng.uniform(-jitter, jitter))


class WSClient:
    """Self-restarting WS-Client.

    Usage::

        client = WSClient(host="127.0.0.1", ports=range(7842, 7853))
        client.set_on_message(handler)
        task = asyncio.create_task(client.run())
        ...
        await client.aclose()
        await task
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        ports: Sequence[int] = tuple(range(7842, 7853)),
        path: str = "/overlay",
        heartbeat_interval_s: float = 1.0,
        heartbeat_timeout_s: float = 3.0,
        on_message: Optional[OnMessage] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        self._host = host
        self._ports = tuple(ports)
        self._path = path
        self._heartbeat_interval = heartbeat_interval_s
        self._heartbeat_timeout = heartbeat_timeout_s
        self._on_message = on_message
        self._rng = rng or random.Random()

        self._stop = asyncio.Event()
        self._connected_evt = asyncio.Event()
        self._last_recv_ns: int = 0
        self._connection_count = 0
        # Outbound buffer for client-originated envelopes (e.g. mascot
        # interaction events). Bounded so a long disconnect cannot grow
        # memory without bound. We drop the OLDEST entry on overflow so
        # the freshest user intent always wins.
        self._outbound: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        self._active_ws: Any = None

    def set_on_message(self, fn: Optional[OnMessage]) -> None:
        self._on_message = fn

    @property
    def connection_count(self) -> int:
        return self._connection_count

    async def aclose(self) -> None:
        self._stop.set()

    async def wait_connected(self, timeout: float | None = None) -> bool:
        try:
            await asyncio.wait_for(self._connected_evt.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    async def send(self, envelope: Any) -> None:
        """Enqueue an outbound envelope for transmission to the server.

        Safe to call before the WS is connected — the envelope sits in
        the bounded outbox until the send-loop is alive. On overflow we
        drop the OLDEST entry so user-driven actions (mascot dbl-click
        mute) are never starved by stale backlog.
        """
        raw = envelope.model_dump_json().encode("utf-8")
        try:
            self._outbound.put_nowait(raw)
        except asyncio.QueueFull:
            try:
                self._outbound.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._outbound.put_nowait(raw)
            except asyncio.QueueFull:
                logger.warning("WSClient outbox still full after evict — drop new")

    async def run(self) -> None:
        """Hauptloop. Returnt nach ``aclose()``."""
        slot = 0
        while not self._stop.is_set():
            ws = await self._try_connect_any()
            if ws is None:
                # Kein Port im Range erreichbar -> Backoff und retry.
                await self._sleep_backoff(slot)
                slot = min(slot + 1, len(BACKOFF_SCHEDULE) - 1)
                continue
            slot = 0  # Reset bei erfolgreicher Connection.
            self._connection_count += 1
            self._connected_evt.set()
            try:
                await self._serve_connection(ws)
            except Exception:  # noqa: BLE001
                logger.exception("WSClient connection loop crashed")
            finally:
                self._connected_evt.clear()
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
            if self._stop.is_set():
                return
            await self._sleep_backoff(slot)
            slot = min(slot + 1, len(BACKOFF_SCHEDULE) - 1)

    async def _try_connect_any(self):
        for port in self._ports:
            if self._stop.is_set():
                return None
            uri = f"ws://{self._host}:{port}{self._path}"
            try:
                ws = await connect(uri, open_timeout=2.0)
                logger.info("WSClient connected to %s", uri)
                return ws
            except (OSError, asyncio.TimeoutError, websockets.WebSocketException):
                continue
        return None

    async def _sleep_backoff(self, slot: int) -> None:
        delay = _backoff_with_jitter(slot, rng=self._rng)
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return  # Backoff abgelaufen, weiter.

    async def _serve_connection(self, ws) -> None:
        loop = asyncio.get_running_loop()
        self._last_recv_ns = loop.time() * 1e9  # nutzt monotonic clock fuers Timeout
        self._active_ws = ws
        hb_task = asyncio.create_task(self._heartbeat_loop(ws), name="ipcws-hb")
        watchdog = asyncio.create_task(self._watchdog_loop(ws), name="ipcws-watchdog")
        sender = asyncio.create_task(self._send_loop(ws), name="ipcws-send")
        try:
            async for raw in ws:
                self._last_recv_ns = loop.time() * 1e9
                try:
                    msg = IPCMessage.validate_json(raw)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("WSClient validate failed: %r", exc)
                    continue
                self._maybe_log_version_drift(msg)
                if self._on_message is not None:
                    try:
                        await self._on_message(msg)
                    except Exception:  # noqa: BLE001
                        logger.exception("on_message raised")
                if self._stop.is_set():
                    break
        finally:
            hb_task.cancel()
            watchdog.cancel()
            sender.cancel()
            for t in (hb_task, watchdog, sender):
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            self._active_ws = None

    async def _send_loop(self, ws) -> None:
        """Drain the outbox into the live socket. Cancelled on disconnect."""
        while not self._stop.is_set():
            raw = await self._outbound.get()
            try:
                await ws.send(raw)
            except Exception:  # noqa: BLE001
                # Connection died mid-send. Re-queue the envelope so the
                # next connection ships it. put_nowait may fail if other
                # items piled up while we slept — accept the drop in that
                # extreme case rather than blocking.
                try:
                    self._outbound.put_nowait(raw)
                except asyncio.QueueFull:
                    logger.debug("WSClient re-enqueue after send error: outbox full")
                return

    async def _heartbeat_loop(self, ws) -> None:
        while not self._stop.is_set():
            try:
                env = HeartbeatEnvelope(payload=HeartbeatPayload(ws_connected=True))
                await ws.send(env.model_dump_json())
            except Exception:  # noqa: BLE001
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._heartbeat_interval)
                return  # stopped
            except asyncio.TimeoutError:
                continue

    async def _watchdog_loop(self, ws) -> None:
        """Schliesst ``ws`` wenn ``heartbeat_timeout_s`` lang nichts kommt.

        Plan §10.5: Das forciert die ``async for`` Schleife im
        ``_serve_connection`` zu beenden, ``run()`` triggert dann den
        Reconnect-Pfad mit Backoff.
        """
        loop = asyncio.get_running_loop()
        check_interval = max(0.05, self._heartbeat_timeout / 3)
        while not self._stop.is_set():
            await asyncio.sleep(check_interval)
            now = loop.time() * 1e9
            if (now - self._last_recv_ns) / 1e9 > self._heartbeat_timeout:
                logger.warning(
                    "WSClient heartbeat-timeout (%.1fs) -> close + reconnect",
                    self._heartbeat_timeout,
                )
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
                return

    def _maybe_log_version_drift(self, msg: object) -> None:
        v = getattr(msg, "v", None)
        if isinstance(v, int) and v != SCHEMA_VERSION:
            logger.warning("schema version drift: incoming v=%d local=%d", v, SCHEMA_VERSION)


__all__ = [
    "BACKOFF_SCHEDULE",
    "JITTER_FRACTION",
    "WSClient",
    "_backoff_with_jitter",
]
