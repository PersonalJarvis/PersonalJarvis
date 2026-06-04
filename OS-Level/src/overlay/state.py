"""OverlayState Enum + StateMachine gemaess Plan §6.

Plan §6.1: 8 States, Glow ist NUR aktiv in TYPING und CLICKING.
Plan AD-8: State-Machine lebt im Overlay-Prozess (nicht im Hauptjarvis).
Plan AD-17: 16 ms Coalescing fuer identische Folge-Transitionen.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class OverlayState(str, Enum):
    """8 States laut Plan §6.1. String-Werte sind stable Wire-Format."""

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


# Plan AD-17 — 16 ms = 1 Frame @ 60 Hz.
COALESCE_WINDOW_NS: int = 16_000_000


# (old, new, reason) — reason kann None sein wenn keine angegeben wurde.
StateChangeCallback = Callable[[OverlayState, OverlayState, Optional[str]], None]


class StateMachine:
    """Authoritative state holder fuer das Overlay.

    Plan AD-8: lebt im Overlay-Prozess. Hauptjarvis sendet Events ueber
    IPC, der EventRouter mappt sie auf ``transition_to()``-Aufrufe.

    Plan §6.2 Coalescing-Regel: ``any -> same state within 16 ms : ignored``.
    Identische Folge-Transitionen innerhalb von ``COALESCE_WINDOW_NS`` sind
    no-op und feuern keine Subscriber.

    Subscriber-Callbacks werden SYNCHRON im Caller-Thread von
    ``transition_to()`` aufgerufen — Subscriber muessen schnell sein und
    nicht blockieren. Wenn ein Callback aus einem Worker-Thread feuert,
    ist Marshalling auf den Qt-Thread Aufgabe des Subscribers (z.B. ueber
    ``QMetaObject.invokeMethod`` oder Qt-Signals).
    """

    def __init__(self, *, initial: OverlayState = OverlayState.IDLE) -> None:
        self._state = initial
        # monotonic_ns() ist immun gegen Wallclock-Skews — hier sicherer
        # als time.time_ns() weil es nur fuer Coalescing-Diff dient.
        self._last_transition_ns: int = 0
        self._lock = threading.RLock()
        self._subscribers: list[StateChangeCallback] = []

    @property
    def state(self) -> OverlayState:
        with self._lock:
            return self._state

    def subscribe(self, callback: StateChangeCallback) -> Callable[[], None]:
        """Registriert einen Subscriber, returnt eine ``unsubscribe``-Funktion."""
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
        """Wechselt nach ``target``. Returnt ``True`` wenn echte Transition.

        Coalescing-Regeln:
        - ``target == current`` und letzter Wechsel < 16 ms -> ignoriert.
        - ``target == current`` aber letzter Wechsel >= 16 ms -> ebenfalls
          ignoriert (kein State-Change), aber das ist kein Coalescing-Hit.
        - ``target != current`` -> immer transitionieren.
        """
        now_ns = time.monotonic_ns()
        with self._lock:
            old = self._state
            if target == old:
                # Kein State-Change. Coalescing-Spec ist hier streng
                # genommen redundant, aber wir loggen den Hit damit
                # Drift-Diagnose moeglich bleibt.
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

        # Subscriber-Dispatch ausserhalb des Locks — verhindert Deadlocks
        # falls ein Subscriber re-entrant transition_to() ruft.
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
        """Plan §17.3 + §20.2 — Convenience fuer Fullscreen-Detector
        und Hide-Timeout-Pfade."""
        return self.transition_to(OverlayState.HIDDEN, reason=reason)


__all__ = [
    "COALESCE_WINDOW_NS",
    "GLOW_ACTIVE_STATES",
    "OverlayState",
    "StateChangeCallback",
    "StateMachine",
]
