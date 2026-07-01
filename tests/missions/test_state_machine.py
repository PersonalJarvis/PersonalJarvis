"""Tests for the state-machine table (legal vs. illegal transitions)."""
from __future__ import annotations

import pytest

from jarvis.missions.state_machine import (
    ALLOWED_TRANSITIONS,
    IllegalStateTransition,
    MissionState,
    MissionType,
    is_terminal,
    transition,
)

# --- Happy-path sequence ---


def test_happy_path_pending_to_approved() -> None:
    """PENDING -> RUNNING -> CRITIQUING -> APPROVED is the success chain."""
    assert transition(MissionState.PENDING, MissionState.RUNNING)
    assert transition(MissionState.RUNNING, MissionState.CRITIQUING)
    assert transition(MissionState.CRITIQUING, MissionState.APPROVED)


def test_simplified_fast_approval_path() -> None:
    """A mission may skip CRITIQUING and flip straight from RUNNING to
    APPROVED when no Critic round is required. Phase-6 worker-critic
    missions still walk via CRITIQUING; the direct edge is whitelisted
    globally."""
    assert transition(MissionState.PENDING, MissionState.RUNNING)
    assert transition(MissionState.RUNNING, MissionState.APPROVED)


# --- MissionType drift guard ---


def test_mission_type_enum_carries_default() -> None:
    members = {member.value for member in MissionType}
    assert members == {"default"}


# --- Critic-Loop ---


def test_critic_loop_critiquing_to_looping_to_running() -> None:
    assert transition(MissionState.CRITIQUING, MissionState.LOOPING)
    assert transition(MissionState.LOOPING, MissionState.RUNNING)


# --- Failure-Pfade ---


@pytest.mark.parametrize(
    "from_state",
    [MissionState.RUNNING, MissionState.CRITIQUING, MissionState.LOOPING],
)
def test_failure_from_active_states(from_state: MissionState) -> None:
    assert transition(from_state, MissionState.FAILED)


# --- Cancel-Pfade ---


@pytest.mark.parametrize(
    "from_state",
    [
        MissionState.PENDING,
        MissionState.RUNNING,
        MissionState.CRITIQUING,
        MissionState.LOOPING,
    ],
)
def test_cancel_from_non_terminal(from_state: MissionState) -> None:
    assert transition(from_state, MissionState.CANCELLED)


# --- Timeout-Pfade ---


@pytest.mark.parametrize(
    "from_state",
    [MissionState.RUNNING, MissionState.CRITIQUING, MissionState.LOOPING],
)
def test_timeout_from_active_states(from_state: MissionState) -> None:
    assert transition(from_state, MissionState.TIMED_OUT)


# --- Illegale Uebergaenge ---


@pytest.mark.parametrize(
    "from_state, to_state",
    [
        # Skip-Ahead
        (MissionState.PENDING, MissionState.APPROVED),
        (MissionState.PENDING, MissionState.CRITIQUING),
        (MissionState.RUNNING, MissionState.LOOPING),  # nur via CRITIQUING
        # NOTE: RUNNING -> APPROVED is the legal direct fast-approval
        # path and was moved out of this illegal-list. Phase-6
        # worker-critic missions still walk via CRITIQUING, but the
        # edge itself is whitelisted globally.
        # Out of a terminal state
        (MissionState.APPROVED, MissionState.RUNNING),
        (MissionState.APPROVED, MissionState.FAILED),
        (MissionState.FAILED, MissionState.RUNNING),
        (MissionState.CANCELLED, MissionState.RUNNING),
        (MissionState.TIMED_OUT, MissionState.RUNNING),
        # Self-loop is not allowed
        (MissionState.RUNNING, MissionState.RUNNING),
        (MissionState.PENDING, MissionState.PENDING),
    ],
)
def test_illegal_transitions_raise(
    from_state: MissionState, to_state: MissionState
) -> None:
    with pytest.raises(IllegalStateTransition):
        transition(from_state, to_state)


# --- Terminal-state classification ---


@pytest.mark.parametrize(
    "state, expected",
    [
        (MissionState.PENDING, False),
        (MissionState.RUNNING, False),
        (MissionState.CRITIQUING, False),
        (MissionState.LOOPING, False),
        (MissionState.APPROVED, True),
        (MissionState.FAILED, True),
        (MissionState.CANCELLED, True),
        (MissionState.TIMED_OUT, True),
    ],
)
def test_is_terminal(state: MissionState, expected: bool) -> None:
    assert is_terminal(state) is expected


# --- Consistency: no ALLOWED transition out of terminal states ---


def test_no_outgoing_transitions_from_terminal_states() -> None:
    terminals = [s for s in MissionState if is_terminal(s)]
    for term in terminals:
        outgoing = [pair for pair in ALLOWED_TRANSITIONS if pair[0] == term]
        assert outgoing == [], f"terminal state {term} has outgoing transitions: {outgoing}"


# --- Consistency: no Phase-5 naming collision (BudgetWarning stays free) ---


def test_no_phase5_budget_warning_collision() -> None:
    """Ensure jarvis/core/events.py:BudgetWarning stays untouched."""
    from jarvis.core.events import BudgetWarning  # Phase-5-Event
    from jarvis.missions.events import MissionBudgetWarning  # Phase-6

    assert BudgetWarning is not MissionBudgetWarning
    assert BudgetWarning.__module__ != MissionBudgetWarning.__module__
