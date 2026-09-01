"""In-process fan-out of stored society envelopes.

The store publishes AFTER the row is on disk (persist-before-publish); the bus
only delivers. Subscribers are observers — a wedged one is abandoned after a
hard timeout and can never block an append (the AP-18 rule of the core bus),
and an exception inside a subscriber is logged, never propagated.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Final

from .events import SocietyEnvelope

log = logging.getLogger(__name__)

__all__ = ["SocietyBus", "SocietyHandler"]

SocietyHandler = Callable[[SocietyEnvelope], Awaitable[None]]

_HANDLER_TIMEOUT_S: Final[float] = 5.0


class SocietyBus:
    def __init__(self) -> None:
        self._handlers: list[SocietyHandler] = []

    def subscribe_all(self, handler: SocietyHandler) -> Callable[[], None]:
        """Register ``handler`` for every envelope; returns its unsubscribe."""
        self._handlers.append(handler)

        def _unsubscribe() -> None:
            if handler in self._handlers:
                self._handlers.remove(handler)

        return _unsubscribe

    @property
    def active_subs(self) -> int:
        return len(self._handlers)

    async def publish(self, envelope: SocietyEnvelope) -> None:
        handlers = list(self._handlers)
        if not handlers:
            return
        await asyncio.gather(
            *(self._safe_dispatch(h, envelope) for h in handlers), return_exceptions=True
        )

    @staticmethod
    async def _safe_dispatch(handler: SocietyHandler, envelope: SocietyEnvelope) -> None:
        try:
            await asyncio.wait_for(handler(envelope), timeout=_HANDLER_TIMEOUT_S)
        except TimeoutError:
            log.warning(
                "society bus: subscriber %s timed out (>%ss) on %s seq=%s and was abandoned",
                getattr(handler, "__qualname__", repr(handler)),
                _HANDLER_TIMEOUT_S,
                envelope.msg_type,
                envelope.seq,
            )
        except Exception:  # noqa: BLE001 — an observer must never break the append path
            log.warning(
                "society bus: subscriber %s failed on %s seq=%s",
                getattr(handler, "__qualname__", repr(handler)),
                envelope.msg_type,
                envelope.seq,
                exc_info=True,
            )
