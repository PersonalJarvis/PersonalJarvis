"""Throttler — Single Source of Truth fuer FPS-Targets + Visibility.

Plan §17.2 + §17.3. Inputs:
  - aktueller OverlayState (idle/listening/.../typing/clicking/...)
  - on_battery (PowerMonitor)
  - last_state_change_ts (von StateMachine subscriber)
  - fullscreen_should_hide (FullscreenDetector)

Outputs:
  - get_target_fps() -> int     (passt FPS-Cap)
  - should_hide_view() -> bool  (5-min-Idle-Pfad: View komplett hidden)
  - subscribe(callback)         (callback feuert wenn target_fps oder
                                  should_hide_view sich aendert)

Plan §17.2 FPS-Targets pro State:
    hidden       -> 0
    idle (30s+)  -> fps_idle (default 1)
    listening / thinking / speaking / error -> fps_active (30)
    typing / clicking -> fps_burst (60)
    error-flash  -> fps_burst fuer 600 ms, dann zurueck

Plan §17.3 Throttling-Strategy:
    AC vs Battery: on_battery -> alle FPS halbieren.
    Idle Detection: 30 s no events -> fps_idle.
    Hide on Idle: 5 min no events -> should_hide_view=True.
    Wake on Event: jeder State-Change resettet Idle-Timer.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Optional

from .state import OverlayState, StateMachine

logger = logging.getLogger(__name__)


# Plan §17.2 + §21.1 default Werte.
DEFAULT_FPS_IDLE: int = 1
DEFAULT_FPS_ACTIVE: int = 30
DEFAULT_FPS_BURST: int = 60
DEFAULT_IDLE_TIMEOUT_S: float = 30.0
DEFAULT_HIDE_TIMEOUT_S: float = 300.0  # 5 Minuten


# State -> FPS-Bucket Mapping. Plan §17.2.
_BURST_STATES: frozenset[OverlayState] = frozenset(
    {OverlayState.TYPING, OverlayState.CLICKING}
)
_ACTIVE_STATES: frozenset[OverlayState] = frozenset(
    {
        OverlayState.LISTENING,
        OverlayState.THINKING,
        OverlayState.SPEAKING,
        OverlayState.ERROR,
    }
)
# IDLE selbst zaehlt als active solange < idle_timeout; danach idle-bucket.


@dataclass(frozen=True)
class ThrottleSnapshot:
    """Aktueller Output. Subscriber bekommen das."""

    target_fps: int
    should_hide_view: bool
    is_hidden_state: bool
    on_battery: bool
    fullscreen_active: bool
    idle_seconds: float


ThrottleCallback = Callable[[ThrottleSnapshot], None]


class Throttler:
    """Berechnet Target-FPS + Hide-Decision aus State + Power + Idle.

    Idempotent + Thread-safe (RLock). ``recompute()`` ist der einzige
    Entry-Point der Subscriber feuert.
    """

    def __init__(
        self,
        machine: StateMachine,
        *,
        fps_idle: int = DEFAULT_FPS_IDLE,
        fps_active: int = DEFAULT_FPS_ACTIVE,
        fps_burst: int = DEFAULT_FPS_BURST,
        idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
        hide_timeout_s: float = DEFAULT_HIDE_TIMEOUT_S,
    ) -> None:
        self._machine = machine
        self._fps_idle = max(0, fps_idle)
        self._fps_active = max(1, fps_active)
        self._fps_burst = max(1, fps_burst)
        self._idle_timeout = idle_timeout_s
        self._hide_timeout = hide_timeout_s

        self._lock = threading.RLock()
        self._last_change_ns: int = time.monotonic_ns()
        self._on_battery: bool = False
        self._fullscreen_should_hide: bool = False
        self._subscribers: list[ThrottleCallback] = []
        self._last_snapshot: Optional[ThrottleSnapshot] = None

        self._unsubscribe_machine = machine.subscribe(self._on_state_change)

    # -------- public Inputs --------

    def set_on_battery(self, on_battery: bool) -> None:
        with self._lock:
            if self._on_battery is on_battery:
                return
            self._on_battery = on_battery
        self.recompute()

    def set_fullscreen_should_hide(self, should_hide: bool) -> None:
        with self._lock:
            if self._fullscreen_should_hide is should_hide:
                return
            self._fullscreen_should_hide = should_hide
        self.recompute()

    def subscribe(self, callback: ThrottleCallback) -> Callable[[], None]:
        """Registriert Callback. Sofort einmal mit aktuellem Snapshot
        aufgerufen damit der Subscriber initial den Zustand kennt.

        Reihenfolge wichtig: erst recompute() (ohne den neuen
        Subscriber in der Liste), dann initial-callback einmal feuern,
        DANN in die Subscriber-Liste aufnehmen. Sonst doppelter
        Initial-Fire (recompute ruft alle Subscriber inkl. dem neuen,
        plus expliziter initial-call hier).
        """
        snap = self.recompute()
        try:
            callback(snap)
        except Exception:  # noqa: BLE001
            logger.exception("Throttler subscriber raised on initial dispatch")
        with self._lock:
            self._subscribers.append(callback)

        def _unsub() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return _unsub

    def shutdown(self) -> None:
        if self._unsubscribe_machine is not None:
            self._unsubscribe_machine()
            self._unsubscribe_machine = None

    # -------- Outputs --------

    @property
    def idle_seconds(self) -> float:
        return (time.monotonic_ns() - self._last_change_ns) / 1e9

    def get_target_fps(self) -> int:
        return self.recompute().target_fps

    def should_hide_view(self) -> bool:
        return self.recompute().should_hide_view

    def recompute(self) -> ThrottleSnapshot:
        """Berechnet den aktuellen Snapshot. Feuert Subscriber wenn
        sich was geaendert hat. Idempotent."""
        with self._lock:
            state = self._machine.state
            idle_s = self.idle_seconds
            on_battery = self._on_battery
            fullscreen_hide = self._fullscreen_should_hide

        is_hidden_state = state is OverlayState.HIDDEN or fullscreen_hide

        if is_hidden_state:
            target = 0
            should_hide_view = True
        else:
            # Idle-State counts as idle-bucket only when stale.
            stale = idle_s >= self._idle_timeout and state is OverlayState.IDLE
            if stale:
                target = self._fps_idle
            elif state in _BURST_STATES:
                target = self._fps_burst
            elif state in _ACTIVE_STATES:
                target = self._fps_active
            else:
                # IDLE within idle_timeout -> active (mascot breathing).
                target = self._fps_active

            # Plan §17.3: AC vs Battery -> halve all targets.
            if on_battery:
                target = max(1, target // 2)

            should_hide_view = (
                state is OverlayState.IDLE and idle_s >= self._hide_timeout
            )
            if should_hide_view:
                # Hidden-View braucht keinen Compositor-Tick.
                target = 0

        snapshot = ThrottleSnapshot(
            target_fps=target,
            should_hide_view=should_hide_view,
            is_hidden_state=is_hidden_state,
            on_battery=on_battery,
            fullscreen_active=fullscreen_hide,
            idle_seconds=idle_s,
        )

        with self._lock:
            prev = self._last_snapshot
            changed = prev is None or _snapshot_changed(prev, snapshot)
            self._last_snapshot = snapshot
            subs = list(self._subscribers) if changed else []

        for cb in subs:
            try:
                cb(snapshot)
            except Exception:  # noqa: BLE001
                logger.exception("Throttler subscriber raised on recompute")

        return snapshot

    # -------- internals --------

    def _on_state_change(
        self, _old: OverlayState, _new: OverlayState, _reason: Optional[str]
    ) -> None:
        # Plan §17.3: Wake on Event -> Idle-Timer reset.
        with self._lock:
            self._last_change_ns = time.monotonic_ns()
        self.recompute()


def _snapshot_changed(a: ThrottleSnapshot, b: ThrottleSnapshot) -> bool:
    """Subscriber feuern nur wenn FPS oder Visibility sich aendern.
    idle_seconds aendert sich kontinuierlich — das wuerde sonst
    Subscriber-Spam ausloesen."""
    return (
        a.target_fps != b.target_fps
        or a.should_hide_view is not b.should_hide_view
        or a.is_hidden_state is not b.is_hidden_state
        or a.on_battery is not b.on_battery
        or a.fullscreen_active is not b.fullscreen_active
    )


# Re-export fuer Subscriber-Tests die ohne expliziten replace() arbeiten.
__all__ = [
    "DEFAULT_FPS_ACTIVE",
    "DEFAULT_FPS_BURST",
    "DEFAULT_FPS_IDLE",
    "DEFAULT_HIDE_TIMEOUT_S",
    "DEFAULT_IDLE_TIMEOUT_S",
    "ThrottleCallback",
    "ThrottleSnapshot",
    "Throttler",
    "replace",
]
