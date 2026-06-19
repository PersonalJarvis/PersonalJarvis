"""Mission time-budget contract (2026-06-10 user mandate).

User mandate: a sub-agent task should complete in ~5-15 minutes on
average and never run much past 20 — while keeping output quality (the
critic loop, preserve-partial-work and grading stay fully active). The
pre-budget reality: per-iteration worker cap 1200 s and mission deadline
4200 s allowed 3 x 20-minute iterations, so complex missions ran 38-49
minutes and users gave up (live missions 019eb27f / 019eb288).

Shape pinned here:
- iteration 0 (the main build) gets the large budget (12 min);
- correction iterations get the short budget (6 min) — they refine an
  existing workspace, they do not rebuild;
- no new correction iteration starts when the remaining task time cannot
  fit a correction + one critic call (the loop ends with the existing
  exhausted semantics instead of overshooting the 20-minute target);
- MAX_CRITIC_LOOPS stays untouched (ADR-0009) — the time guard is an
  additional bound, not a loop-count change.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.missions.kontrollierer import orchestrator as orch_mod
from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import MissionState
from tests.missions.kontrollierer.test_loop import (
    FakeCriticRunner,
    FakeWorker,
    _make_approve_verdict,
    _make_kontrollierer,
    _make_revise_verdict,
)


@pytest.fixture
async def manager(tmp_path: Path):
    m = MissionManager(tmp_path / "missions.db")
    await m.start()
    yield m
    await m.stop()


def test_budget_constants_pin() -> None:
    """Documented time-budget shape — change only with a new user mandate."""
    assert orch_mod._ITER0_WORKER_TIMEOUT_S == 720.0
    assert orch_mod._CORRECTION_WORKER_TIMEOUT_S == 360.0
    assert orch_mod._MISSION_DEADLINE_S == 1500.0
    assert orch_mod._TASK_TIME_BUDGET_S == 1380.0


@pytest.mark.asyncio
async def test_iter0_gets_main_budget_corrections_get_short_budget(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Worker spawns carry degressive timeouts: 720 s, then 360 s."""
    worker = FakeWorker()
    critic = FakeCriticRunner(_make_revise_verdict(), _make_approve_verdict())
    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    mid = await manager.dispatch(prompt="build the thing")
    end_state = await k.run_mission(mid)

    assert end_state == MissionState.APPROVED
    assert len(worker.spawn_calls) == 2
    assert worker.spawn_calls[0]["timeout_s"] == 720.0
    assert worker.spawn_calls[1]["timeout_s"] == 360.0


@pytest.mark.asyncio
async def test_no_new_iteration_when_time_budget_exhausted(
    manager: MissionManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no correction fits the remaining budget, the loop ends instead
    of overshooting — existing exhausted semantics, only one worker spawn."""
    monkeypatch.setattr(orch_mod, "_TASK_TIME_BUDGET_S", 0.0)
    worker = FakeWorker()
    critic = FakeCriticRunner(_make_revise_verdict())
    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    mid = await manager.dispatch(prompt="build the thing")
    end_state = await k.run_mission(mid)

    assert end_state == MissionState.FAILED
    assert len(worker.spawn_calls) == 1, (
        "no second iteration may start once the time budget is spent"
    )


def test_worker_error_transient_matcher_knows_subscription_limits() -> None:
    """Live incident 019eb2fd (2026-06-10 21:23): the worker died with
    "You've hit your session limit · resets 11:10pm (Europe/Berlin)" AFTER
    writing the complete deliverable. The transient matcher only knew
    rate-limit/429/overloaded phrasings, so the mission discarded finished
    work as task_error instead of grading it. Subscription-window limits
    (Claude "session limit", ChatGPT "usage limit", codex "out of credits")
    are transient by nature — the window resets.
    """
    transient = orch_mod._worker_error_is_transient
    assert transient("You've hit your session limit · resets 11:10pm (Europe/Berlin)")
    assert transient("You've hit your usage limit. Upgrade to Pro or try again at 7:40 PM.")
    assert transient("overageStatus: rejected, out_of_credits")
    assert transient("429 Too Many Requests")
    assert not transient("Compilation failed: missing semicolon")
    assert not transient("")
