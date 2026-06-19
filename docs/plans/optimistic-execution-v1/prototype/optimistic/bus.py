"""Event bus — the in-process async fan-out backbone.

Mirrors `jarvis/core/bus.py`: a lightweight pub/sub where every event is fanned
out to all interested handlers. Key properties:

- **No broker, no network**: the in-process EventBus IS the queue (AD-OE2 /
  cloud-first €5-VPS doctrine — no Redis, RabbitMQ, or Celery required).
- **Fault isolation**: a handler that raises is caught, logged, and swallowed;
  it must never break the publisher or other handlers (AP-18 parity).
- **Ordering**: typed subscribers run first (in registration order), wildcard
  (subscribe_all) subscribers run after — so the flight recorder always sees
  events after domain handlers have run.
- **isinstance matching**: `subscribe(BaseClass, h)` catches all subclasses,
  enabling broad "any event" subscriptions without needing subscribe_all.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from optimistic.events import Event

_log = logging.getLogger("optimistic.bus")

# Type alias for an async event handler.
Handler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """Async pub/sub event bus (standard-library only).

    Usage::

        bus = EventBus()
        bus.subscribe(MissionSpawn, worker._on_mission_spawn)
        bus.subscribe_all(flight_log._record)
        await bus.publish(MissionSpawn(command="...", ...))
    """

    def __init__(self) -> None:
        # Typed subscriptions: list of (event_type, handler) pairs.
        # A list of pairs (rather than a dict keyed by type) preserves
        # registration order naturally and makes isinstance matching trivial.
        self._typed: list[tuple[type[Event], Handler]] = []
        # Wildcard handlers — receive every event after typed handlers.
        self._all: list[Handler] = []

    # ------------------------------------------------------------------
    # Subscription API
    # ------------------------------------------------------------------

    def subscribe(self, event_type: type[Event], handler: Handler) -> None:
        """Register *handler* to be called for every event that is an
        instance of *event_type* (including subclasses).

        Handlers are called in registration order.
        """
        self._typed.append((event_type, handler))

    def subscribe_all(self, handler: Handler) -> None:
        """Register *handler* as a wildcard subscriber — it receives every
        published event regardless of type.  Called after all typed handlers.

        Intended for flight recorders and audit logs (production pattern:
        the flight recorder is a wildcard subscriber over the real bus).
        """
        self._all.append(handler)

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish(self, event: Event) -> None:
        """Fan-out *event* to all matching typed handlers, then to all
        wildcard handlers.

        Guarantee: if a handler raises, the exception is caught, logged at
        ERROR level via ``logging.getLogger("optimistic.bus")``, and silently
        swallowed.  All remaining handlers still run.  This function itself
        never raises.
        """
        # Phase 1 — typed subscribers (in registration order)
        for event_type, handler in self._typed:
            if isinstance(event, event_type):
                await self._safe_call(handler, event)

        # Phase 2 — wildcard subscribers (in registration order)
        for handler in self._all:
            await self._safe_call(handler, event)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _safe_call(handler: Handler, event: Event) -> None:
        """Call *handler(event)* and swallow any exception it raises."""
        try:
            await handler(event)
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "EventBus handler %r raised %s: %s",
                getattr(handler, "__qualname__", handler),
                type(exc).__name__,
                exc,
                exc_info=True,
            )
