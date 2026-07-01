"""OverlayState enum + StateMachine per Plan §6.

Plan §6.1: 8 states, glow is ONLY active in TYPING and CLICKING.
Plan AD-8: the state machine lives in the overlay process (not in Main-Jarvis).
Plan AD-17: 16 ms coalescing for identical consecutive transitions.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class OverlayState(str, Enum):
    """8 states per Plan §6.1. String values are the stable wire format."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    TYPING = "typing"
    CLICKING = "clicking"
    SPEAKING = "speaking"
    ERROR = "error"
    HIDDEN = "hidden"


GLOW_ACTIVE_STATES: frozenset[OverlayState] = frozenset(
    {OverlayState.TYPING, OverlayState.CLICKING}
)


# Plan AD-17 — 16 ms = 1 frame @ 60 Hz.
COALESCE_WINDOW_NS: int = 16_000_000


# (old, new, reason) — reason can be None if none was given.
StateChangeCallback = Callable[[OverlayState, OverlayState, Optional[str]], None]


class StateMachine:
    """Authoritative state holder for the overlay.

    Plan AD-8: lives in the overlay process. Main-Jarvis sends events
    over IPC, and the EventRouter maps them to ``transition_to()`` calls.

    Plan §6.2 coalescing rule: ``any -> same state within 16 ms : ignored``.
    Identical consecutive transitions within ``COALESCE_WINDOW_NS`` are
    a no-op and do not fire subscribers.

    Subscriber callbacks are called SYNCHRONOUSLY on the caller thread
    of ``transition_to()`` — subscribers must be fast and non-blocking.
    If a callback fires from a worker thread, marshalling onto the Qt
    thread is the subscriber's responsibility (e.g. via
    ``QMetaObject.invokeMethod`` or Qt signals).
    """

    def __init__(self, *, initial: OverlayState = OverlayState.IDLE) -> None:
        self._state = initial
        # monotonic_ns() is immune to wallclock skew — safer here than
        # time.time_ns() since it's only used for the coalescing diff.
        self._last_transition_ns: int = 0
        self._lock = threading.RLock()
        self._subscribers: list[StateChangeCallback] = []

    @property
    def state(self) -> OverlayState:
        with self._lock:
            return self._state

    def subscribe(self, callback: StateChangeCallback) -> Callable[[], None]:
        """Registers a subscriber, returns an ``unsubscribe`` function."""
        with self._lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return _unsubscribe

    def transition_to(
        self,
        target: OverlayState,
        *,
        reason: Optional[str] = None,
    ) -> bool:
        """Switches to ``target``. Returns ``True`` if it was a real transition.

        Coalescing rules:
        - ``target == current`` and the last change was < 16 ms ago -> ignored.
        - ``target == current`` but the last change was >= 16 ms ago -> also
          ignored (no state change), but that is not a coalescing hit.
        - ``target != current`` -> always transitions.
        """
        now_ns = time.monotonic_ns()
        with self._lock:
            old = self._state
            if target == old:
                # No state change. The coalescing spec is strictly
                # speaking redundant here, but we log the hit so
                # drift diagnosis stays possible.
                if (now_ns - self._last_transition_ns) < COALESCE_WINDOW_NS:
                    logger.debug(
                        "state coalesced: %s -> %s within %d ns",
                        old.value,
                        target.value,
                        now_ns - self._last_transition_ns,
                    )
                return False
            self._state = target
            self._last_transition_ns = now_ns
            subscribers_snapshot = list(self._subscribers)

        # Subscriber dispatch outside the lock — prevents deadlocks
        # in case a subscriber re-entrantly calls transition_to().
        for cb in subscribers_snapshot:
            try:
                cb(old, target, reason)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "StateMachine subscriber raised on %s -> %s",
                    old.value,
                    target.value,
                )
        return True


    def transition_to_hidden(self, *, reason: Optional[str] = None) -> bool:
        """Plan §17.3 + §20.2 — convenience for the fullscreen detector
        and hide-timeout paths."""
        return self.transition_to(OverlayState.HIDDEN, reason=reason)


__all__ = [
    "COALESCE_WINDOW_NS",
    "GLOW_ACTIVE_STATES",
    "OverlayState",
    "StateChangeCallback",
    "StateMachine",
]
