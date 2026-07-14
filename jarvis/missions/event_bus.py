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

# Hard cap on how long a wildcard handler (registered via ``subscribe_all`` —
# bridging to the global bus, telemetry/flight-recorder-style subscribers) may
# take before ``publish`` abandons it for THIS dispatch. Mirrors
# ``jarvis.core.bus.EventBus._WILDCARD_HANDLER_TIMEOUT_S`` (AP-18,
# BUG-CU-STALL): a plain ``await handler(envelope)`` loop has no bound, so one
# wedged wildcard handler (a stalled WebSocket forward, a dead channel API
# call) freezes ``publish`` — and with it the publishing mission — forever,
# because the caller's concurrency-limiting semaphore slot is never freed.
_WILDCARD_HANDLER_TIMEOUT_S = 5.0


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
        Wildcard handlers are dispatched in PARALLEL (`asyncio.gather`), each
        under a hard timeout, with errors logged but never propagated — true
        parity with `jarvis.core.bus.EventBus._safe_dispatch` (AP-18,
        BUG-CU-STALL): a single wedged wildcard handler (a stalled WebSocket
        forward, a dead channel API call) can no longer block `publish` — and
        with it the publishing mission — forever.
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

        handlers = list(self._wildcard_handlers)
        if handlers:
            await asyncio.gather(
                *(self._safe_dispatch(h, envelope) for h in handlers),
                return_exceptions=True,
            )

    @staticmethod
    async def _safe_dispatch(
        handler: Callable[[EventEnvelope], Awaitable[None]], envelope: EventEnvelope
    ) -> None:
        try:
            await asyncio.wait_for(
                handler(envelope), timeout=_WILDCARD_HANDLER_TIMEOUT_S
            )
        except TimeoutError:
            log.warning(
                "MissionBus wildcard handler TIMED OUT (>%ss) and was abandoned "
                "— handler=%s (AP-18: an observer must never block the bus)",
                _WILDCARD_HANDLER_TIMEOUT_S,
                getattr(handler, "__qualname__", repr(handler)),
            )
        except Exception:
            log.exception("MissionBus: wildcard handler error discarded")

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
