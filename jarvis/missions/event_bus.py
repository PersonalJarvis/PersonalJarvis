"""Per-subscriber bounded-queue event bus for the mission subsystem.

Unlike the Phase-0-5 `jarvis.core.bus.EventBus` (direct callback dispatch),
this bus uses a dedicated `asyncio.Queue` per subscriber. Consequence:
a slow subscriber (e.g. a WebSocket client with backpressure) does NOT block
the voice critical path — it receives drop-oldest losses on its own queue.

Wildcard `subscribe_all` is intended for (a) bridging to the global bus and
(b) telemetry / flight-recorder-style subscribers — errors are logged but never
propagated.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from .events import EventEnvelope

log = logging.getLogger(__name__)


@dataclass
class Subscription:
    """An active bus subscription with its own bounded queue."""

    queue: asyncio.Queue[EventEnvelope]
    filter_fn: Callable[[EventEnvelope], bool] | None = None
    dropped: int = 0

    async def __aiter__(self) -> AsyncIterator[EventEnvelope]:
        """Convenience `async for envelope in subscription` loop."""
        while True:
            yield await self.queue.get()


class MissionBus:
    """Per-subscriber bounded-queue event bus.

    `maxsize` applies per subscription. When the queue is full: drop-oldest, then put.
    Wildcard handlers receive every event directly (awaited call, no queue).
    """

    def __init__(self, *, maxsize: int = 1024) -> None:
        self._maxsize = maxsize
        self._subscriptions: list[Subscription] = []
        self._wildcard_handlers: list[Callable[[EventEnvelope], Awaitable[None]]] = []

    async def publish(self, envelope: EventEnvelope) -> None:
        """Dispatch to all matching subscribers + wildcard handlers.

        Slow per-queue path: drops the oldest element, then put_nowait.
        Wildcard errors are logged but never propagated (Phase-0-5 pattern
        from `jarvis.core.bus.EventBus._safe_dispatch`).
        """
        for sub in list(self._subscriptions):
            if sub.filter_fn is not None and not sub.filter_fn(envelope):
                continue
            try:
                sub.queue.put_nowait(envelope)
            except asyncio.QueueFull:
                # drop-oldest, then put again
                try:
                    sub.queue.get_nowait()
                    sub.dropped += 1
                except asyncio.QueueEmpty:
                    # a concurrent consumer just drained the queue
                    pass
                try:
                    sub.queue.put_nowait(envelope)
                except asyncio.QueueFull:
                    # Queue is still full (very tight) — event lost
                    sub.dropped += 1

        for handler in list(self._wildcard_handlers):
            try:
                await handler(envelope)
            except Exception:
                log.exception("MissionBus: Wildcard-Handler-Fehler verworfen")

    @asynccontextmanager
    async def subscribe(
        self,
        filter_fn: Callable[[EventEnvelope], bool] | None = None,
    ) -> AsyncIterator[Subscription]:
        """Async context manager for a subscription with its own queue.

        Usage:
            async with bus.subscribe() as sub:
                async for envelope in sub:
                    ...
        """
        sub = Subscription(
            queue=asyncio.Queue(maxsize=self._maxsize),
            filter_fn=filter_fn,
        )
        self._subscriptions.append(sub)
        try:
            yield sub
        finally:
            if sub in self._subscriptions:
                self._subscriptions.remove(sub)

    def subscribe_all(
        self, handler: Callable[[EventEnvelope], Awaitable[None]]
    ) -> Callable[[], None]:
        """Wildcard subscription. Returns an unsubscribe callable.

        For bridging to the global bus or telemetry. Errors are swallowed in
        `publish()` — a broken wildcard handler does not break the stream.
        """
        self._wildcard_handlers.append(handler)

        def unsubscribe() -> None:
            if handler in self._wildcard_handlers:
                self._wildcard_handlers.remove(handler)

        return unsubscribe

    @property
    def active_subs(self) -> int:
        return len(self._subscriptions)

    @property
    def queue_depths(self) -> dict[int, int]:
        """`id(sub) -> queue depth`. For BusStats events."""
        return {id(sub): sub.queue.qsize() for sub in self._subscriptions}

    @property
    def dropped_counts(self) -> dict[int, int]:
        """`id(sub) -> drop counter`. For BusStats events."""
        return {id(sub): sub.dropped for sub in self._subscriptions}
