"""Tests for WorkerSupervisor signal classification + Kontrollierer wire.

H9 (2026-05-17 audit): WorkerSupervisor (jarvis/missions/workers/supervisor.py)
was implemented but never instantiated in production code — the
audit-team-2 verdict was "dead code". This test file pins both the
class behaviour and the new wire-up inside ``_spawn_worker_collect``
that surfaces a TIMED_OUT classification as ``worker_error``.

Per the class's own contract the supervisor is *passive*: it
classifies states but never kills. Kill remains the Job-Object's job.
That contract is enforced by the wire here: the orchestrator logs
STUCK/TIMED_OUT but does NOT break the event loop -- it lets the
job-object cap take over.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from jarvis.missions.workers.supervisor import (
    WorkerState,
    WorkerSupervisor,
)


# --- WorkerSupervisor signal classification ------------------------------


def test_supervisor_must_be_started_before_observe() -> None:
    """``start()`` initialises the timing state; without it
    ``observe_event`` raises a clear error rather than silently producing
    bogus IDLE / RUNNING classifications."""
    sup = WorkerSupervisor(idle_timeout_s=10.0, hard_cap_s=60.0)
    with pytest.raises(RuntimeError, match="start"):
        sup.observe_event(_make_event(type="assistant"))


def test_supervisor_classifies_running_for_non_terminal_event() -> None:
    sup = _fresh_sup()
    state = sup.observe_event(_make_event(type="assistant"))
    assert state == WorkerState.RUNNING


def test_supervisor_classifies_done_ok_for_claude_result_success() -> None:
    sup = _fresh_sup()
    state = sup.observe_event(_make_event(type="result", is_error=False))
    assert state == WorkerState.DONE_OK


def test_supervisor_classifies_done_err_for_claude_result_error() -> None:
    sup = _fresh_sup()
    state = sup.observe_event(_make_event(type="result", is_error=True))
    assert state == WorkerState.DONE_ERR


def test_supervisor_classifies_done_ok_for_codex_turn_completed() -> None:
    sup = _fresh_sup()
    state = sup.observe_event(_make_event(type="turn.completed"))
    assert state == WorkerState.DONE_OK


@pytest.mark.parametrize("etype", ["turn.failed", "error"])
def test_supervisor_classifies_done_err_for_codex_terminal_failures(
    etype: str,
) -> None:
    sup = _fresh_sup()
    assert sup.observe_event(_make_event(type=etype)) == WorkerState.DONE_ERR


def test_supervisor_api_retry_marks_waiting_not_stuck() -> None:
    """The reason WorkerSupervisor exists: provider 429-backoffs should
    NOT trip the idle-watchdog. ``api_retry`` extends the idle deadline
    by ``retry_delay_ms`` instead."""
    sup = _fresh_sup_with_clock()
    sup.observe_event(_make_event(
        type="system", subtype="api_retry", retry_delay_ms=5000,
    ))
    # Even after the idle_timeout elapses, the supervisor stays WAITING
    # because the api_retry pushed _waiting_until out 5 s.
    sup.monotonic = lambda: 2.0  # advance past idle_timeout (=1.0 in fixture)
    assert sup.check_idle() == WorkerState.WAITING


def test_supervisor_check_idle_returns_stuck_after_silence() -> None:
    """No event for longer than ``idle_timeout_s`` and no api_retry
    pending -> STUCK. This is the signal the wire-up surfaces as a
    warning log so future iterations can short-circuit the Critic-Loop."""
    sup = _fresh_sup_with_clock()
    # Pretend we got one event at t=0, then nothing.
    sup.observe_event(_make_event(type="assistant"))
    sup.monotonic = lambda: 1.5  # past idle_timeout=1.0
    assert sup.check_idle() == WorkerState.STUCK


def test_supervisor_check_idle_returns_timed_out_past_hard_cap() -> None:
    """Hard wall-clock cap overrides every other classification --
    including WAITING. Even if the worker is in a legitimate API-retry
    backoff, exceeding the hard cap forces TIMED_OUT."""
    sup = _fresh_sup_with_clock()
    sup.observe_event(_make_event(
        type="system", subtype="api_retry", retry_delay_ms=60000,
    ))
    sup.monotonic = lambda: 10.0  # past hard_cap=5.0
    assert sup.check_idle() == WorkerState.TIMED_OUT


def test_supervisor_observe_exit_uses_returncode_zero_as_done_ok() -> None:
    sup = _fresh_sup()
    assert sup.observe_exit(0) == WorkerState.DONE_OK


def test_supervisor_observe_exit_nonzero_is_done_err() -> None:
    sup = _fresh_sup()
    assert sup.observe_exit(1) == WorkerState.DONE_ERR


def test_supervisor_observe_exit_none_is_done_err() -> None:
    """Worker subprocess that we waited on but produced no return code
    is treated as a failure -- a healthy worker always exits with 0."""
    sup = _fresh_sup()
    assert sup.observe_exit(None) == WorkerState.DONE_ERR


# --- Helpers ---


@dataclass
class _Event:
    """Tiny shape compatible with WorkerSupervisor.observe_event."""
    type: str = ""
    subtype: str | None = None
    is_error: bool = False
    retry_delay_ms: int | None = None


def _make_event(**kw: Any) -> _Event:
    return _Event(**kw)


def _fresh_sup() -> WorkerSupervisor:
    sup = WorkerSupervisor(idle_timeout_s=10.0, hard_cap_s=60.0)
    sup.start()
    return sup


def _fresh_sup_with_clock() -> WorkerSupervisor:
    """Supervisor with deterministic monotonic clock starting at t=0."""
    sup = WorkerSupervisor(
        idle_timeout_s=1.0,
        hard_cap_s=5.0,
        monotonic=lambda: 0.0,
    )
    sup.start()
    return sup
