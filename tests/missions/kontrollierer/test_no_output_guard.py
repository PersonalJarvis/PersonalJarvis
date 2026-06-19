"""Regression tests for the 2026-05-29 sub-agent robustness fix:
``Kontrollierer.run_mission`` is idempotent against COMPLETED missions
(APPROVED success / user-CANCELLED) so a stale re-dispatch from the REST or
voice path can't replay a second created->plan->approved lifecycle or re-spend.

Error states (FAILED / TIMED_OUT) intentionally stay re-runnable so a
crash_recovery'd mission can still be retried to completion — see
``test_recovery_then_rerun_is_idempotent`` in ``test_recovery.py``.

Uses a light hand-rolled fake (fakes-not-mock convention) and builds the
Kontrollierer via ``object.__new__`` so it exercises the real method without
standing up the full mission stack.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from jarvis.missions.kontrollierer.orchestrator import Kontrollierer
from jarvis.missions.state_machine import MissionState


@dataclass
class _FakeView:
    state: MissionState
    prompt: str = "create a file"


class _FakeManager:
    """Records whether the orchestrator tried to drive the mission past the
    terminal guard (transition_state is the first call after it)."""

    def __init__(self, view: _FakeView) -> None:
        self._view = view
        self.transition_calls = 0

    async def mission(self, mission_id: str) -> _FakeView:
        return self._view

    async def transition_state(self, *a, **k) -> None:  # pragma: no cover
        self.transition_calls += 1


def _make_kontrollierer(view: _FakeView) -> tuple[Kontrollierer, _FakeManager]:
    k = object.__new__(Kontrollierer)
    mgr = _FakeManager(view)
    k._manager = mgr
    k._state_locks = {}
    k._running_missions = {}  # run_mission tracks its in-flight task here
    return k, mgr


@pytest.mark.parametrize(
    "done_state",
    [MissionState.APPROVED, MissionState.CANCELLED],
)
def test_run_mission_skips_completed_mission(done_state: MissionState) -> None:
    """An APPROVED (success, idempotent) or CANCELLED (user aborted) mission
    must return its state immediately and NOT re-drive the state machine — no
    duplicate created->plan->approved lifecycle, no re-spend."""
    k, mgr = _make_kontrollierer(_FakeView(state=done_state))
    result = asyncio.run(k.run_mission("mission-xyz"))
    assert result == done_state
    assert mgr.transition_calls == 0, (
        "completed mission must not be transitioned/re-run"
    )


@pytest.mark.parametrize(
    "error_state",
    [MissionState.FAILED, MissionState.TIMED_OUT],
)
def test_run_mission_still_runs_error_state(error_state: MissionState) -> None:
    """A FAILED / TIMED_OUT mission must NOT be short-circuited — the recovery
    path needs to be able to retry it to completion. The guard only skips
    APPROVED/CANCELLED, so an error-state mission proceeds to the first
    transition (which our fake records)."""
    k, mgr = _make_kontrollierer(_FakeView(state=error_state))
    # The bare instance has no _decomposer/_manager.store, so run_mission
    # raises somewhere AFTER the guard. That's fine — the point is the guard
    # let it THROUGH: the first RUNNING transition fired, proving an error
    # state is NOT short-circuited the way APPROVED/CANCELLED is. A clean
    # crash_recovery'd mission must stay retryable to completion.
    with pytest.raises(Exception):  # noqa: B017 — any failure past the guard
        asyncio.run(k.run_mission("mission-xyz"))
    assert mgr.transition_calls >= 1, (
        "error-state mission must stay re-runnable (guard must not skip it)"
    )


def test_run_mission_unknown_mission_raises() -> None:
    """A None view (truly unknown id) still raises KeyError — the guard must
    not swallow that distinct error path."""

    class _NoneManager:
        async def mission(self, mission_id: str):
            return None

    k = object.__new__(Kontrollierer)
    k._manager = _NoneManager()
    k._state_locks = {}
    k._running_missions = {}  # run_mission tracks its in-flight task here
    with pytest.raises(KeyError):
        asyncio.run(k.run_mission("ghost"))
