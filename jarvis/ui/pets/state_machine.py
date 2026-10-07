"""Which animation the pet shows, derived from the overlay's coarse modes.

The bridge already tells every overlay what to *be* through the bar's coarse
modes (``jarvis.ui.jarvisbar.modes``). The pet maps them onto its own states
(``jarvis.ui.pets.states``) and adds what a mode cannot say: the one-shot
outcomes (``success`` / ``error``), what Jarvis is doing inside a turn (the
action states ``working`` / ``searching``, set from tool calls), an agent task
running in the background, the user holding the pet with the mouse (``held``),
and falling asleep after a long quiet spell.

Precedence, highest first: held, a one-shot, the mode's own state - except
that ``thinking`` and ``idle`` give way to a live action, and ``idle`` to
background work (shown as ``working``) - and finally sleep.

The machine is pure and clock-driven: nothing here starts a timer. The
overlay asks :meth:`PetStateMachine.state` when it paints and sleeps for
:meth:`PetStateMachine.next_change_in` at most, so an idle pet wakes up only
when something can actually change.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from jarvis.ui.jarvisbar.modes import NOTICE_MODES
from jarvis.ui.pets.states import (
    ACTION_HOLD_SECONDS,
    ACTION_STATES,
    ONE_SHOT_SECONDS,
    ONE_SHOT_STATES,
    SLEEP_AFTER_SECONDS,
)

_log = logging.getLogger(__name__)

#: Coarse overlay mode -> the pet state it shows. ``notice`` is not here: it
#: plays the ``error`` one-shot on top of whatever the pet was doing.
MODE_STATES: dict[str, str] = {
    "idle": "idle",
    "listen": "listening",
    "dictate": "listening",
    "think": "thinking",
    "dictate_transcribing": "thinking",
    "speak": "talking",
}

#: The one-shot a ``notice`` mode plays.
NOTICE_OUTCOME = "error"


class PetStateMachine:
    """Turns overlay modes, outcomes and activity into one pet state."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        now = clock()
        self._base = "idle"
        self._last_activity = now
        self._one_shot: str | None = None
        self._one_shot_until = now
        self._action: str | None = None
        self._action_until = now
        self._busy = False
        self._held = False
        self._shown = "idle"
        self._shown_since = now

    # -- inputs ---------------------------------------------------------

    def on_mode(self, mode: str) -> None:
        """Follow the overlay's coarse mode (``jarvis.ui.jarvisbar.modes``)."""
        if mode in NOTICE_MODES:
            self.on_outcome(NOTICE_OUTCOME)
            return
        state = MODE_STATES.get(mode)
        if state is None:
            _log.debug("pet state machine ignores unknown mode %r", mode)
            return
        now = self._clock()
        self._settle(now)
        self._last_activity = now
        self._base = state
        if state != "thinking":
            # An action is a step of the thinking phase; talking or the end of
            # the turn closes it.
            self._action = None
        self._set_shown(self._wanted(), now)

    def on_outcome(self, kind: str) -> None:
        """Play the ``success`` or ``error`` one-shot, then return to the mode state."""
        if kind not in ONE_SHOT_STATES:
            _log.debug("pet state machine ignores unknown outcome %r", kind)
            return
        now = self._clock()
        self._settle(now)
        self._last_activity = now
        self._one_shot = kind
        self._one_shot_until = now + ONE_SHOT_SECONDS[kind]
        if self._held:
            return
        # Set directly, not through _set_shown: a repeated outcome restarts
        # its animation even though the state name does not change.
        self._shown = kind
        self._shown_since = now

    def on_action(self, kind: str | None) -> None:
        """A tool step started (``working`` / ``searching``), or ``None``: it ended."""
        if kind is not None and kind not in ACTION_STATES:
            _log.debug("pet state machine ignores unknown action %r", kind)
            return
        now = self._clock()
        self._settle(now)
        self._last_activity = now
        self._action = kind
        self._action_until = now + ACTION_HOLD_SECONDS
        self._set_shown(self._wanted(), now)

    def on_busy(self, busy: bool) -> None:
        """An agent task is (or is no longer) running in the background."""
        now = self._clock()
        self._settle(now)
        self._last_activity = now
        self._busy = bool(busy)
        self._set_shown(self._wanted(), now)

    def on_held(self, held: bool) -> None:
        """The user picked the pet up with the mouse, or let go."""
        now = self._clock()
        self._settle(now)
        self._last_activity = now
        self._held = bool(held)
        self._set_shown(self._wanted(), now)

    def on_activity(self) -> None:
        """Any Jarvis event: wake from sleep and restart the sleep countdown."""
        now = self._clock()
        self._settle(now)
        self._last_activity = now
        self._set_shown(self._wanted(), now)

    # -- outputs --------------------------------------------------------

    def state(self) -> str:
        """The state to show now (one of ``PET_STATES``)."""
        self._settle(self._clock())
        return self._shown

    def state_started_at(self) -> float:
        """Clock time the current state began; animations count frames from here."""
        self._settle(self._clock())
        return self._shown_since

    def next_change_in(self) -> float | None:
        """Seconds until a timer-driven change, or ``None`` when none is pending.

        Timer-driven changes are the end of a one-shot, the end of an action
        whose result never arrived, and falling asleep.
        """
        now = self._clock()
        self._settle(now)
        due: list[float] = []
        if self._one_shot is not None:
            due.append(self._one_shot_until - now)
        if self._action is not None:
            due.append(self._action_until - now)
        if self._can_sleep() and self._shown != "sleeping":
            due.append(self._last_activity + SLEEP_AFTER_SECONDS - now)
        return max(0.0, min(due)) if due else None

    # -- internals ------------------------------------------------------

    def _settle(self, now: float) -> None:
        """Apply the timer-driven changes that fell due by ``now``.

        Each change is stamped with the moment it fell due, not the moment
        someone asked, so a late poll does not shift the animation's phase.
        """
        if self._one_shot is not None and now >= self._one_shot_until:
            self._one_shot = None
            self._set_shown(self._wanted(), self._one_shot_until)
        if self._action is not None and now >= self._action_until:
            self._action = None
            self._set_shown(self._wanted(), max(self._action_until, self._shown_since))
        if self._can_sleep():
            asleep_at = self._last_activity + SLEEP_AFTER_SECONDS
            if now >= asleep_at:
                self._set_shown("sleeping", max(asleep_at, self._shown_since))

    def _wanted(self) -> str:
        """The state the inputs ask for, before sleep (see the module docstring)."""
        if self._held:
            return "held"
        if self._one_shot is not None:
            return self._one_shot
        if self._base in ("thinking", "idle") and self._action is not None:
            return self._action
        if self._base == "idle" and self._busy:
            return "working"
        return self._base

    def _can_sleep(self) -> bool:
        """Only a pet with nothing to show may doze off."""
        return self._wanted() == "idle"

    def _set_shown(self, state: str, since: float) -> None:
        if state != self._shown:
            self._shown = state
            self._shown_since = since
