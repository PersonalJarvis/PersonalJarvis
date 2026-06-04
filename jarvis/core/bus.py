"""Async Event Bus — local pub/sub infrastructure.

Responsibilities:
- Decouples layers (L2 Speech publishes UtteranceCaptured, L3 Intent subscribes).
- Trace-ID-based correlation for debug replay.
- Delivers events to the flight recorder (when enabled).

Can be swapped for a Redis bus later when running multi-process.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .events import Event

E = TypeVar("E", bound=Event)
Handler = Callable[[Event], Awaitable[None]]

# Hard cap on how long a *wildcard* subscriber (an observer/fan-out handler
# registered via ``subscribe_all`` -- WebSocket forwarders, channel adapters,
# recorders) may take before the bus abandons it for THIS dispatch.
#
# Rationale (BUG-CU-STALL, 2026-05-29 / AP-18): ``publish`` awaits every
# subscriber via ``asyncio.gather``. A wildcard observer that blocks -- e.g. a
# WebSocket ``send_json`` to a stalled/half-open browser tab, or a channel API
# call to a dead socket -- has NO natural bound and would freeze the *entire*
# event bus. Because the voice and Computer-Use dispatch paths publish through
# this bus (``ActionProposed``, ``HarnessDispatched``), one wedged browser tab
# silently froze a Computer-Use mission for ~28 s until the voice idle-timeout
# hung up -- "stood ready, did nothing". The module docstring of ``publish``
# already promised "a broken subscriber must never block the pipeline";
# this enforces it for the observer class that can actually block.
#
# Typed subscribers (``subscribe(EventType, ...)``) are deliberately NOT capped:
# a publisher may legitimately depend on them, and some (the TTS announcement
# handler) await audio playback for several seconds by design. The cap is
# generous enough that no healthy local observer is ever cut, yet far below the
# voice idle-timeout so a genuine wedge can never reach it.
_WILDCARD_HANDLER_TIMEOUT_S = 5.0


class EventBus:
    """In-process asyncio event bus with per-event-class topic subscriptions."""

    def __init__(self) -> None:
        self._subscribers: dict[type[Event], list[Handler]] = defaultdict(list)
        self._wildcard_subscribers: list[Handler] = []
        self._lock = asyncio.Lock()

    def subscribe(self, event_type: type[E], handler: Callable[[E], Awaitable[None]]) -> None:
        """Register a handler for a specific event type."""
        self._subscribers[event_type].append(handler)  # type: ignore[arg-type]

    def subscribe_all(self, handler: Handler) -> None:
        """Register a handler that receives EVERY event — e.g. flight recorder or metrics."""
        self._wildcard_subscribers.append(handler)

    def unsubscribe(self, event_type: type[E], handler: Callable[[E], Awaitable[None]]) -> None:
        handlers = self._subscribers.get(event_type)
        if handlers and handler in handlers:  # type: ignore[operator]
            handlers.remove(handler)  # type: ignore[arg-type]

    async def publish(self, event: Event) -> None:
        """Deliver an event in parallel to all matching subscribers.

        Subscriber errors are logged but not propagated — a broken
        subscriber must never block the pipeline.
        """
        event_type = type(event)
        typed: list[Handler] = list(self._subscribers.get(event_type, []))
        wildcard: list[Handler] = list(self._wildcard_subscribers)
        if not typed and not wildcard:
            return

        # Parallel dispatch; collect exceptions but do not propagate. Wildcard
        # (observer/fan-out) handlers get a hard timeout so a wedged one cannot
        # freeze the bus — and with it the voice/Computer-Use dispatch paths
        # (AP-18, BUG-CU-STALL). Typed handlers are awaited uncapped on purpose.
        results = await asyncio.gather(
            *(self._safe_dispatch(h, event) for h in typed),
            *(self._safe_dispatch(h, event, timeout_s=_WILDCARD_HANDLER_TIMEOUT_S)
              for h in wildcard),
            return_exceptions=True,
        )
        # Exceptions are logged in _safe_dispatch — results here only for completeness
        _ = results

    @staticmethod
    async def _safe_dispatch(
        handler: Handler, event: Event, *, timeout_s: float | None = None
    ) -> None:
        try:
            if timeout_s is None:
                await handler(event)
            else:
                await asyncio.wait_for(handler(event), timeout=timeout_s)
        except TimeoutError:
            # A wildcard observer wedged (e.g. a WebSocket send to a stalled
            # browser tab). Abandon it for this dispatch — the bus must never
            # block on an observer (AP-18). Name the culprit so this is never
            # an invisible 28 s stall again.
            from loguru import logger

            logger.warning(
                "EventBus wildcard subscriber TIMED OUT (>{}s) and was "
                "abandoned — handler={} event={} (AP-18: an observer must "
                "never block the bus)",
                timeout_s,
                getattr(handler, "__qualname__", repr(handler)),
                type(event).__name__,
            )
        except Exception as exc:  # noqa: BLE001
            # Lazy import to avoid circular imports
            from loguru import logger

            logger.opt(exception=exc).warning(
                "EventBus subscriber failed",
                event=type(event).__name__,
                trace_id=str(event.trace_id),
            )


# Global default bus (usually overridden via DI/registry)
_default_bus: EventBus | None = None


def get_default_bus() -> EventBus:
    """Singleton accessor for cases where DI would have too much overhead."""
    global _default_bus
    if _default_bus is None:
        _default_bus = EventBus()
    return _default_bus


def reset_default_bus() -> None:
    """For tests only."""
    global _default_bus
    _default_bus = None
