"""Throttler — Plan §17.2 + §17.3 FPS-Targets + Hide-Logic."""

from __future__ import annotations

import time

import pytest

from overlay.state import OverlayState, StateMachine
from overlay.throttler import (
    DEFAULT_FPS_ACTIVE,
    DEFAULT_FPS_BURST,
    DEFAULT_FPS_IDLE,
    Throttler,
)


@pytest.fixture()
def machine() -> StateMachine:
    return StateMachine()


# -------------------------------------------------------------------------
# State -> FPS Mapping
# -------------------------------------------------------------------------


def test_initial_state_idle_returns_active_fps(machine: StateMachine) -> None:
    """IDLE within idle_timeout -> active FPS (mascot still breathing)."""
    t = Throttler(machine)
    assert t.get_target_fps() == DEFAULT_FPS_ACTIVE


def test_typing_uses_burst_fps(machine: StateMachine) -> None:
    t = Throttler(machine)
    machine.transition_to(OverlayState.TYPING)
    assert t.get_target_fps() == DEFAULT_FPS_BURST


def test_clicking_uses_burst_fps(machine: StateMachine) -> None:
    t = Throttler(machine)
    machine.transition_to(OverlayState.CLICKING)
    assert t.get_target_fps() == DEFAULT_FPS_BURST


def test_listening_uses_active_fps(machine: StateMachine) -> None:
    t = Throttler(machine)
    machine.transition_to(OverlayState.LISTENING)
    assert t.get_target_fps() == DEFAULT_FPS_ACTIVE


def test_hidden_state_uses_zero_fps(machine: StateMachine) -> None:
    t = Throttler(machine)
    machine.transition_to(OverlayState.HIDDEN)
    snapshot = t.recompute()
    assert snapshot.target_fps == 0
    assert snapshot.should_hide_view is True


# -------------------------------------------------------------------------
# Power: AC vs Battery halbiert
# -------------------------------------------------------------------------


def test_battery_halves_burst_fps(machine: StateMachine) -> None:
    t = Throttler(machine)
    machine.transition_to(OverlayState.TYPING)
    assert t.get_target_fps() == DEFAULT_FPS_BURST  # 60
    t.set_on_battery(True)
    assert t.get_target_fps() == DEFAULT_FPS_BURST // 2  # 30


def test_battery_halves_active_fps(machine: StateMachine) -> None:
    t = Throttler(machine)
    machine.transition_to(OverlayState.SPEAKING)
    t.set_on_battery(True)
    assert t.get_target_fps() == DEFAULT_FPS_ACTIVE // 2


def test_back_to_ac_restores_full_fps(machine: StateMachine) -> None:
    t = Throttler(machine)
    machine.transition_to(OverlayState.TYPING)
    t.set_on_battery(True)
    assert t.get_target_fps() == 30
    t.set_on_battery(False)
    assert t.get_target_fps() == 60


# -------------------------------------------------------------------------
# Idle-Timeout
# -------------------------------------------------------------------------


def test_idle_timeout_drops_to_idle_fps(machine: StateMachine) -> None:
    """Plan §17.3 — 30 s no events -> fps_idle. Wir nutzen 50 ms zum Testen."""
    t = Throttler(machine, idle_timeout_s=0.05)
    # IDLE start, sofort: noch active.
    assert t.get_target_fps() == DEFAULT_FPS_ACTIVE
    time.sleep(0.07)
    assert t.get_target_fps() == DEFAULT_FPS_IDLE


def test_state_change_resets_idle_timer(machine: StateMachine) -> None:
    t = Throttler(machine, idle_timeout_s=0.05)
    time.sleep(0.07)
    assert t.get_target_fps() == DEFAULT_FPS_IDLE  # idle bucket
    machine.transition_to(OverlayState.LISTENING)
    # Nach State-Change ist der Timer reset -> nicht mehr idle bucket.
    assert t.get_target_fps() == DEFAULT_FPS_ACTIVE


# -------------------------------------------------------------------------
# Hide-Timeout
# -------------------------------------------------------------------------


def test_hide_timeout_sets_should_hide_view(machine: StateMachine) -> None:
    """Plan §17.3 — 5 min no events -> IsVisible False."""
    t = Throttler(machine, idle_timeout_s=0.02, hide_timeout_s=0.06)
    time.sleep(0.08)
    snap = t.recompute()
    assert snap.should_hide_view is True
    assert snap.target_fps == 0


def test_state_change_un_hides(machine: StateMachine) -> None:
    t = Throttler(machine, idle_timeout_s=0.02, hide_timeout_s=0.06)
    time.sleep(0.08)
    assert t.recompute().should_hide_view is True
    machine.transition_to(OverlayState.LISTENING)
    assert t.recompute().should_hide_view is False


# -------------------------------------------------------------------------
# Fullscreen-Hide
# -------------------------------------------------------------------------


def test_fullscreen_hide_overrides_state(machine: StateMachine) -> None:
    t = Throttler(machine)
    machine.transition_to(OverlayState.TYPING)
    assert t.get_target_fps() == DEFAULT_FPS_BURST
    t.set_fullscreen_should_hide(True)
    snap = t.recompute()
    assert snap.target_fps == 0
    assert snap.should_hide_view is True
    assert snap.is_hidden_state is True


def test_fullscreen_release_returns_to_state_fps(machine: StateMachine) -> None:
    t = Throttler(machine)
    machine.transition_to(OverlayState.TYPING)
    t.set_fullscreen_should_hide(True)
    assert t.get_target_fps() == 0
    t.set_fullscreen_should_hide(False)
    assert t.get_target_fps() == DEFAULT_FPS_BURST


# -------------------------------------------------------------------------
# Subscriber feuert nur bei echtem Wechsel
# -------------------------------------------------------------------------


def test_subscriber_fires_on_initial(machine: StateMachine) -> None:
    fired = []
    t = Throttler(machine)
    t.subscribe(lambda s: fired.append(s))
    assert len(fired) == 1


def test_subscriber_does_not_fire_on_idle_seconds_drift(
    machine: StateMachine,
) -> None:
    """idle_seconds aendert sich kontinuierlich — ohne Wechsel der
    target_fps oder should_hide_view kein Subscriber-Spam."""
    fired = []
    t = Throttler(machine)
    t.subscribe(lambda s: fired.append(s))
    initial_count = len(fired)
    # 5x recompute in kurzer Folge — keine echten Wechsel.
    for _ in range(5):
        t.recompute()
    assert len(fired) == initial_count


def test_subscriber_fires_on_battery_transition(machine: StateMachine) -> None:
    fired = []
    t = Throttler(machine)
    t.subscribe(lambda s: fired.append(s))
    initial = len(fired)
    t.set_on_battery(True)
    assert len(fired) > initial


def test_subscriber_fires_on_state_change(machine: StateMachine) -> None:
    fired = []
    t = Throttler(machine)
    t.subscribe(lambda s: fired.append(s))
    initial = len(fired)
    machine.transition_to(OverlayState.TYPING)
    # Muss mind. einmal mehr feuern.
    assert len(fired) > initial
