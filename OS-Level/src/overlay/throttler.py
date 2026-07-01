"""Throttler — single source of truth for FPS targets + visibility.

Plan §17.2 + §17.3. Inputs:
  - current OverlayState (idle/listening/.../typing/clicking/...)
  - on_battery (PowerMonitor)
  - last_state_change_ts (from the StateMachine subscriber)
  - fullscreen_should_hide (FullscreenDetector)

Outputs:
  - get_target_fps() -> int     (the FPS cap)
  - should_hide_view() -> bool  (5-min idle path: view fully hidden)
  - subscribe(callback)         (callback fires when target_fps or
                                  should_hide_view changes)

Plan §17.2 FPS targets per state:
    hidden       -> 0
    idle (30s+)  -> fps_idle (default 1)
    listening / thinking / speaking / error -> fps_active (30)
    typing / clicking -> fps_burst (60)
    error-flash  -> fps_burst for 600 ms, then back

Plan §17.3 throttling strategy:
    AC vs battery: on_battery -> halve all FPS.
    Idle detection: 30 s no events -> fps_idle.
    Hide on idle: 5 min no events -> should_hide_view=True.
    Wake on event: every state change resets the idle timer.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Optional

from .state import OverlayState, StateMachine

logger = logging.getLogger(__name__)


# Plan §17.2 + §21.1 default values.
DEFAULT_FPS_IDLE: int = 1
DEFAULT_FPS_ACTIVE: int = 30
DEFAULT_FPS_BURST: int = 60
DEFAULT_IDLE_TIMEOUT_S: float = 30.0
DEFAULT_HIDE_TIMEOUT_S: float = 300.0  # 5 minutes


# State -> FPS bucket mapping. Plan §17.2.
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
# IDLE itself counts as active as long as < idle_timeout; after that, idle bucket.


@dataclass(frozen=True)
class ThrottleSnapshot:
    """Current output. Subscribers receive this."""

    target_fps: int
    should_hide_view: bool
    is_hidden_state: bool
    on_battery: bool
    fullscreen_active: bool
    idle_seconds: float


ThrottleCallback = Callable[[ThrottleSnapshot], None]


class Throttler:
    """Computes the target FPS + hide decision from state + power + idle.

    Idempotent + thread-safe (RLock). ``recompute()`` is the only
    entry point that fires subscribers.
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
        """Registers the callback. Immediately called once with the
        current snapshot so the subscriber knows the initial state.

        Order matters: first recompute() (without the new subscriber
        in the list), then fire the initial callback once, THEN add
        it to the subscriber list. Otherwise it would fire twice
        initially (recompute calls all subscribers including the new
        one, plus the explicit initial call here).
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
        """Computes the current snapshot. Fires subscribers if
        something changed. Idempotent."""
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
                # A hidden view doesn't need a compositor tick.
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
        # Plan §17.3: wake on event -> idle timer reset.
        with self._lock:
            self._last_change_ns = time.monotonic_ns()
        self.recompute()


def _snapshot_changed(a: ThrottleSnapshot, b: ThrottleSnapshot) -> bool:
    """Subscribers only fire if FPS or visibility change.
    idle_seconds changes continuously — firing on that would
    otherwise trigger subscriber spam."""
    return (
        a.target_fps != b.target_fps
        or a.should_hide_view is not b.should_hide_view
        or a.is_hidden_state is not b.is_hidden_state
        or a.on_battery is not b.on_battery
        or a.fullscreen_active is not b.fullscreen_active
    )


# Re-export for subscriber tests that work without an explicit replace().
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
