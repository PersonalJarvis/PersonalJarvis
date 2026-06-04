"""Web channel: WebSocket-based ChannelAdapter for the web UI.

Sits at L5.5 and connects the FastAPI WebSocket route
(:mod:`jarvis.ui.web.routes_ws`) with the global :class:`EventBus`.

Flow:
- ``start()`` attaches a wildcard subscriber to the bus. Every event is
  wrapped in a JSON envelope and broadcast to all connected sessions.
- ``register()`` is called by the WS endpoint for each new connection and
  returns a :class:`ChannelSession`.
- ``receive()`` accepts incoming JSON frames, builds a :class:`ChannelMessage`
  from them, and places it in the inbox queue (``messages()``).
- ``send_message()`` sends a server-side-generated message to the associated
  session (or broadcasts when ``session_id`` is not set).
- ``stop()`` unsubscribes and closes all sockets.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from loguru import logger

from jarvis.core.bus import EventBus
from jarvis.core.events import ErrorOccurred, Event

from .base import ChannelMessage, ChannelSession

if TYPE_CHECKING:  # pragma: no cover
    pass

__all__ = ["WebChannel"]


class WebChannel:
    """ChannelAdapter implementation for the FastAPI WebSocket route."""

    name = "web"

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._clients: dict[UUID, Any] = {}           # session_id -> WebSocket
        self._sessions: dict[UUID, ChannelSession] = {}
        self._inbox: asyncio.Queue[ChannelMessage] = asyncio.Queue()
        self._started = False
        self._event_handler_ref: Any = None           # for unsubscribe

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        self._event_handler_ref = self._on_event
        self._bus.subscribe_all(self._event_handler_ref)
        self._started = True
        logger.debug("WebChannel started; wildcard subscriber attached")

    async def stop(self) -> None:
        if not self._started:
            return
        # Remove wildcard subscriber (the bus has no public API for this,
        # so we manipulate the list directly — intentionally tight coupling).
        wildcards = getattr(self._bus, "_wildcard_subscribers", None)
        if wildcards is not None and self._event_handler_ref in wildcards:
            wildcards.remove(self._event_handler_ref)
        self._event_handler_ref = None

        # Close all WebSockets
        for session_id, ws in list(self._clients.items()):
            try:
                await ws.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("WebChannel close failed for {}: {}", session_id, exc)
        self._clients.clear()
        self._sessions.clear()
        self._started = False
        logger.debug("WebChannel stopped")

    # ------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------

    async def register(self, ws: Any, user_handle: str = "", locale: str = "de") -> ChannelSession:
        """Registers a WebSocket connection and returns the session."""
        session_id = uuid4()
        session = ChannelSession(
            session_id=session_id,
            channel_name=self.name,
            user_handle=user_handle,
            locale=locale,
        )
        self._clients[session_id] = ws
        self._sessions[session_id] = session
        logger.debug("WebChannel session registered: {}", session_id)
        return session

    async def unregister(self, session_id: UUID) -> None:
        self._clients.pop(session_id, None)
        self._sessions.pop(session_id, None)
        logger.debug("WebChannel session unregistered: {}", session_id)

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    async def receive(self, session_id: UUID, raw: dict[str, Any]) -> None:
        """Translates an incoming JSON frame into a ChannelMessage."""
        try:
            kind = raw.get("kind", "text")
            if kind not in {"text", "voice", "system", "action", "event_mirror"}:
                kind = "text"
            msg = ChannelMessage(
                session_id=session_id,
                kind=kind,  # type: ignore[arg-type]
                content=str(raw.get("content", "")),
                metadata={k: v for k, v in raw.items() if k not in {"kind", "content"}},
            )
            await self._inbox.put(msg)
        except Exception as exc:  # noqa: BLE001
            # Defensive: publish the error as an event rather than re-raising.
            logger.opt(exception=exc).warning(
                "WebChannel.receive failed for session {}", session_id
            )
            await self._bus.publish(
                ErrorOccurred(
                    source_layer="channel.web",
                    layer="channel.web",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    recoverable=True,
                )
            )

    async def messages(self) -> AsyncIterator[ChannelMessage]:
        """Async iterator of inbound messages (for the orchestrator consumer)."""
        while True:
            msg = await self._inbox.get()
            yield msg

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send_message(self, msg: ChannelMessage) -> None:
        """Sends a message to a session (or broadcasts when session_id is nil)."""
        payload = {
            "type": "channel_message",
            "kind": msg.kind,
            "content": msg.content,
            "trace_id": str(msg.trace_id),
            "session_id": str(msg.session_id),
            "timestamp_ns": msg.timestamp_ns,
            "metadata": msg.metadata,
        }
        targets: list[tuple[UUID, Any]]
        if msg.session_id in self._clients:
            targets = [(msg.session_id, self._clients[msg.session_id])]
        else:
            targets = list(self._clients.items())
        await self._send_to(targets, payload)

    async def broadcast_event(self, event: Event) -> None:
        """Serialize an event to all connected WebSockets."""
        envelope = self._event_envelope(event)
        targets = list(self._clients.items())
        await self._send_to(targets, envelope)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    async def sessions(self) -> list[ChannelSession]:
        return list(self._sessions.values())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _on_event(self, event: Event) -> None:
        """Wildcard subscriber: mirror every event to all clients."""
        try:
            await self.broadcast_event(event)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "WebChannel._on_event failed for {}", type(event).__name__
            )

    def _event_envelope(self, event: Event) -> dict[str, Any]:
        """Serializes an event for the wire — preferably via the central helper
        from ``jarvis.ui.web.schema``; falls back to a defensive inline
        implementation on import error."""
        try:
            from jarvis.ui.web.schema import event_to_ws_envelope  # noqa: WPS433

            return event_to_ws_envelope(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("event_to_ws_envelope unavailable ({}); using fallback", exc)
            return {
                "type": "event",
                "event_type": type(event).__name__,
                "trace_id": str(getattr(event, "trace_id", "")),
                "timestamp_ns": getattr(event, "timestamp_ns", 0),
                "source_layer": getattr(event, "source_layer", ""),
            }

    async def _send_to(
        self, targets: list[tuple[UUID, Any]], payload: dict[str, Any]
    ) -> None:
        dead: list[UUID] = []
        for session_id, ws in targets:
            try:
                await ws.send_json(payload)
            except Exception as exc:  # noqa: BLE001
                logger.debug("WebChannel send_json failed for {}: {}", session_id, exc)
                dead.append(session_id)
        for session_id in dead:
            await self.unregister(session_id)
