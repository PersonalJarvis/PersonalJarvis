"""The pet's state machine, driven by a fake clock."""

from __future__ import annotations

import pytest

from jarvis.ui.jarvisbar.modes import MODES
from jarvis.ui.pets.state_machine import MODE_STATES, PetStateMachine
from jarvis.ui.pets.states import (
    ACTION_HOLD_SECONDS,
    ACTION_STATES,
    ONE_SHOT_SECONDS,
    PET_STATES,
    SLEEP_AFTER_SECONDS,
)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def machine(clock: FakeClock) -> PetStateMachine:
    return PetStateMachine(clock=clock)


def test_starts_idle(machine: PetStateMachine, clock: FakeClock) -> None:
    assert machine.state() == "idle"
    assert machine.state_started_at() == clock.now


@pytest.mark.parametrize(
    ("mode", "state"),
    [
        ("idle", "idle"),
        ("listen", "listening"),
        ("dictate", "listening"),
        ("think", "thinking"),
        ("dictate_transcribing", "thinking"),
        ("speak", "talking"),
    ],
)
def test_every_mode_maps_to_a_state(machine: PetStateMachine, mode: str, state: str) -> None:
    machine.on_mode(mode)
    assert machine.state() == state


def test_every_overlay_mode_is_handled() -> None:
    # Every mode is a state mapping or the notice one-shot; nothing is dropped.
    assert set(MODES) - set(MODE_STATES) == {"notice"}
    assert set(MODE_STATES.values()) <= set(PET_STATES)


def test_notice_plays_the_error_one_shot(machine: PetStateMachine, clock: FakeClock) -> None:
    machine.on_mode("listen")
    machine.on_mode("notice")
    assert machine.state() == "error"
    clock.advance(ONE_SHOT_SECONDS["error"])
    assert machine.state() == "listening"


def test_unknown_mode_and_outcome_are_ignored(machine: PetStateMachine) -> None:
    machine.on_mode("speak")
    machine.on_mode("dancing")
    machine.on_outcome("confetti")
    assert machine.state() == "talking"


def test_state_change_restarts_the_animation_clock(
    machine: PetStateMachine, clock: FakeClock
) -> None:
    clock.advance(5)
    machine.on_mode("think")
    assert machine.state_started_at() == clock.now
    clock.advance(1)
    machine.on_mode("think")  # same state: the animation keeps its phase
    assert machine.state_started_at() == clock.now - 1


@pytest.mark.parametrize("kind", ["success", "error"])
def test_one_shot_returns_to_the_mode_state(
    machine: PetStateMachine, clock: FakeClock, kind: str
) -> None:
    machine.on_mode("speak")
    machine.on_outcome(kind)
    assert machine.state() == kind
    assert machine.next_change_in() == pytest.approx(ONE_SHOT_SECONDS[kind])
    clock.advance(ONE_SHOT_SECONDS[kind] - 0.01)
    assert machine.state() == kind
    clock.advance(0.02)
    assert machine.state() == "talking"


def test_mode_change_during_a_one_shot_applies_after_it(
    machine: PetStateMachine, clock: FakeClock
) -> None:
    machine.on_outcome("success")
    machine.on_mode("listen")
    assert machine.state() == "success"
    clock.advance(ONE_SHOT_SECONDS["success"])
    assert machine.state() == "listening"


def test_one_shot_end_is_stamped_when_it_fell_due(
    machine: PetStateMachine, clock: FakeClock
) -> None:
    machine.on_outcome("success")
    due = clock.now + ONE_SHOT_SECONDS["success"]
    clock.advance(10)  # a late poll
    assert machine.state() == "idle"
    assert machine.state_started_at() == due


def test_repeated_outcome_restarts_its_animation(
    machine: PetStateMachine, clock: FakeClock
) -> None:
    machine.on_outcome("error")
    clock.advance(1)
    machine.on_outcome("error")
    assert machine.state_started_at() == clock.now


def test_falls_asleep_after_quiet_idle(machine: PetStateMachine, clock: FakeClock) -> None:
    assert machine.next_change_in() == pytest.approx(SLEEP_AFTER_SECONDS)
    clock.advance(SLEEP_AFTER_SECONDS - 1)
    assert machine.state() == "idle"
    clock.advance(1)
    assert machine.state() == "sleeping"
    assert machine.next_change_in() is None


def test_activity_wakes_and_resets_the_countdown(
    machine: PetStateMachine, clock: FakeClock
) -> None:
    clock.advance(SLEEP_AFTER_SECONDS + 5)
    assert machine.state() == "sleeping"
    machine.on_activity()
    assert machine.state() == "idle"
    assert machine.next_change_in() == pytest.approx(SLEEP_AFTER_SECONDS)


def test_a_session_never_falls_asleep(machine: PetStateMachine, clock: FakeClock) -> None:
    machine.on_mode("listen")
    assert machine.next_change_in() is None
    clock.advance(SLEEP_AFTER_SECONDS * 3)
    assert machine.state() == "listening"


def test_sleep_countdown_starts_when_the_session_ends(
    machine: PetStateMachine, clock: FakeClock
) -> None:
    machine.on_mode("speak")
    clock.advance(SLEEP_AFTER_SECONDS * 2)
    machine.on_mode("idle")
    assert machine.state() == "idle"
    clock.advance(SLEEP_AFTER_SECONDS)
    assert machine.state() == "sleeping"


def test_outcome_while_asleep_wakes_then_returns_to_idle(
    machine: PetStateMachine, clock: FakeClock
) -> None:
    clock.advance(SLEEP_AFTER_SECONDS)
    assert machine.state() == "sleeping"
    machine.on_outcome("success")
    assert machine.state() == "success"
    clock.advance(ONE_SHOT_SECONDS["success"])
    assert machine.state() == "idle"


# --- Action states, background work and holding ------------------------------


def test_a_tool_step_shows_its_action_while_thinking(machine: PetStateMachine) -> None:
    machine.on_mode("think")
    machine.on_action("searching")
    assert machine.state() == "searching"
    machine.on_action("working")
    assert machine.state() == "working"
    machine.on_action(None)
    assert machine.state() == "thinking"


def test_talking_closes_the_action(machine: PetStateMachine) -> None:
    machine.on_mode("think")
    machine.on_action("working")
    machine.on_mode("speak")
    assert machine.state() == "talking"
    machine.on_mode("idle")
    assert machine.state() == "idle"


def test_an_action_without_a_result_expires(machine: PetStateMachine, clock: FakeClock) -> None:
    machine.on_mode("think")
    machine.on_action("working")
    assert machine.next_change_in() == pytest.approx(ACTION_HOLD_SECONDS)
    clock.advance(ACTION_HOLD_SECONDS)
    assert machine.state() == "thinking"
    assert machine.state_started_at() == clock.now


def test_an_unknown_action_is_ignored(machine: PetStateMachine) -> None:
    machine.on_mode("think")
    machine.on_action("juggling")
    assert machine.state() == "thinking"


def test_background_work_keeps_an_idle_pet_working_and_awake(
    machine: PetStateMachine, clock: FakeClock
) -> None:
    machine.on_busy(True)
    assert machine.state() == "working"
    assert machine.next_change_in() is None
    clock.advance(SLEEP_AFTER_SECONDS * 2)
    assert machine.state() == "working"
    machine.on_mode("listen")
    assert machine.state() == "listening"
    machine.on_mode("idle")
    assert machine.state() == "working"
    machine.on_busy(False)
    assert machine.state() == "idle"


def test_holding_beats_everything_and_letting_go_restores(machine: PetStateMachine) -> None:
    machine.on_mode("speak")
    machine.on_held(True)
    assert machine.state() == "held"
    machine.on_outcome("success")
    assert machine.state() == "held"
    machine.on_held(False)
    assert machine.state() == "success"


def test_a_held_pet_never_falls_asleep(machine: PetStateMachine, clock: FakeClock) -> None:
    machine.on_held(True)
    clock.advance(SLEEP_AFTER_SECONDS * 2)
    assert machine.state() == "held"


def test_every_new_state_is_a_pet_state() -> None:
    assert set(ACTION_STATES) | {"held"} <= set(PET_STATES)
