"""StateMachine — Plan §6 + AD-8 + AD-17.

Tests fuer Coalescing, Subscriber-Dispatch, Re-entrancy.
"""

from __future__ import annotations

import threading
import time

import pytest

from overlay.state import (
    COALESCE_WINDOW_NS,
    GLOW_ACTIVE_STATES,
    OverlayState,
    StateMachine,
)


# -------------------------------------------------------------------------
# Konstruktion + Initial-State
# -------------------------------------------------------------------------


def test_initial_state_default_idle() -> None:
    sm = StateMachine()
    assert sm.state is OverlayState.IDLE


def test_initial_state_override() -> None:
    sm = StateMachine(initial=OverlayState.HIDDEN)
    assert sm.state is OverlayState.HIDDEN


# -------------------------------------------------------------------------
# transition_to + Subscribers
# -------------------------------------------------------------------------


def test_transition_changes_state_and_returns_true() -> None:
    sm = StateMachine()
    assert sm.transition_to(OverlayState.LISTENING) is True
    assert sm.state is OverlayState.LISTENING


def test_transition_to_same_state_returns_false() -> None:
    sm = StateMachine(initial=OverlayState.IDLE)
    assert sm.transition_to(OverlayState.IDLE) is False
    assert sm.state is OverlayState.IDLE


def test_subscriber_fires_on_transition() -> None:
    sm = StateMachine()
    events: list[tuple[OverlayState, OverlayState, str | None]] = []
    sm.subscribe(lambda old, new, reason: events.append((old, new, reason)))

    sm.transition_to(OverlayState.LISTENING, reason="wakeword")
    sm.transition_to(OverlayState.THINKING, reason="user")

    assert events == [
        (OverlayState.IDLE, OverlayState.LISTENING, "wakeword"),
        (OverlayState.LISTENING, OverlayState.THINKING, "user"),
    ]


def test_subscriber_does_not_fire_on_no_change() -> None:
    sm = StateMachine()
    fired: list[OverlayState] = []
    sm.subscribe(lambda _o, n, _r: fired.append(n))

    sm.transition_to(OverlayState.IDLE)
    assert fired == []


def test_unsubscribe_stops_callbacks() -> None:
    sm = StateMachine()
    fired: list[OverlayState] = []
    unsub = sm.subscribe(lambda _o, n, _r: fired.append(n))

    sm.transition_to(OverlayState.LISTENING)
    unsub()
    sm.transition_to(OverlayState.THINKING)

    assert fired == [OverlayState.LISTENING]


def test_subscriber_exception_does_not_break_others() -> None:
    sm = StateMachine()
    fired: list[OverlayState] = []

    def boom(_o: OverlayState, _n: OverlayState, _r: str | None) -> None:
        raise RuntimeError("subscriber crash")

    sm.subscribe(boom)
    sm.subscribe(lambda _o, n, _r: fired.append(n))

    assert sm.transition_to(OverlayState.LISTENING) is True
    assert fired == [OverlayState.LISTENING]


def test_subscriber_runs_in_caller_thread() -> None:
    sm = StateMachine()
    seen_thread: list[int] = []
    sm.subscribe(lambda _o, _n, _r: seen_thread.append(threading.get_ident()))

    main_ident = threading.get_ident()
    sm.transition_to(OverlayState.LISTENING)
    assert seen_thread == [main_ident]


def test_reentrant_transition_is_safe() -> None:
    """Subscriber darf state lesen UND wieder transition_to rufen."""

    sm = StateMachine()
    chain: list[OverlayState] = []

    def chained(_old: OverlayState, new: OverlayState, _r: str | None) -> None:
        chain.append(new)
        if new is OverlayState.LISTENING:
            # Re-entrant Call — RLock muss das tragen.
            sm.transition_to(OverlayState.THINKING, reason="chained")

    sm.subscribe(chained)
    sm.transition_to(OverlayState.LISTENING)

    assert chain == [OverlayState.LISTENING, OverlayState.THINKING]
    assert sm.state is OverlayState.THINKING


# -------------------------------------------------------------------------
# Coalescing — AD-17
# -------------------------------------------------------------------------


def test_same_state_within_coalesce_window_returns_false() -> None:
    sm = StateMachine()
    sm.transition_to(OverlayState.LISTENING)
    # Sofort nochmal LISTENING -> coalesced.
    assert sm.transition_to(OverlayState.LISTENING) is False


def test_different_state_within_coalesce_window_transitions() -> None:
    sm = StateMachine()
    sm.transition_to(OverlayState.LISTENING)
    # Andere Ziel-State innerhalb 16 ms -> immer durchlassen.
    assert sm.transition_to(OverlayState.THINKING) is True
    assert sm.state is OverlayState.THINKING


def test_coalesce_window_is_16_ms() -> None:
    # Sanity: Konstante = 16 ms.
    assert COALESCE_WINDOW_NS == 16_000_000


def test_same_state_after_coalesce_window_still_returns_false() -> None:
    """Plan §6.2: kein State-Change bleibt no-op auch nach 16 ms."""

    sm = StateMachine()
    sm.transition_to(OverlayState.LISTENING)
    # 25 ms warten — ueber dem Coalescing-Window — aber gleicher Target.
    time.sleep(0.025)
    assert sm.transition_to(OverlayState.LISTENING) is False


# -------------------------------------------------------------------------
# Glow-State-Set
# -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state, glow_expected",
    [
        (OverlayState.IDLE, False),
        (OverlayState.LISTENING, False),
        (OverlayState.THINKING, False),
        (OverlayState.TYPING, True),
        (OverlayState.CLICKING, True),
        (OverlayState.SPEAKING, False),
        (OverlayState.ERROR, False),
        (OverlayState.HIDDEN, False),
    ],
)
def test_glow_active_states_match_plan(
    state: OverlayState, glow_expected: bool
) -> None:
    assert (state in GLOW_ACTIVE_STATES) is glow_expected
