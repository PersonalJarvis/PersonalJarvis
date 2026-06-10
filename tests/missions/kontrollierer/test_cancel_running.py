"""Tests for externally cancelling an in-flight ``run_mission`` task.

The REST cancel endpoint flips the DB state to CANCELLED, but historically
the Kontrollierer's ``run_mission`` background task kept running — workers
kept burning tokens until they slammed into the (now terminal)
state-transition wall. ``cancel_running_mission`` cancels the tracked
asyncio task; the TaskGroup propagates the cancellation and the per-worker
Job-Object context managers close on exit, killing the subprocesses — the
same proven teardown path the wall-clock mission timeout already uses
(orchestrator.py run_mission TimeoutError branch).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jarvis.missions.budget import BudgetTracker
from jarvis.missions.kontrollierer.orchestrator import Kontrollierer
from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import MissionState

from tests.missions.kontrollierer.test_loop import (
    FakeCriticRunner,
    FakeJobObject,
    FakeWorker,
    FakeWorktreeManager,
    _make_approve_verdict,
    _make_kontrollierer,
)


@pytest.fixture
async def manager(tmp_path: Path):
    m = MissionManager(tmp_path / "missions.db")
    await m.start()
    yield m
    await m.stop()


class HangingDecomposer:
    """Decomposer stub that blocks forever — until the task is cancelled."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self._release = asyncio.Event()  # never set

    async def decompose(self, prompt: str) -> Any:
        self.entered.set()
        await self._release.wait()
        raise AssertionError("unreachable — decompose must be cancelled")


def _make_hanging_kontrollierer(
    *, manager: MissionManager, tmp_path: Path, decomposer: HangingDecomposer
) -> Kontrollierer:
    return Kontrollierer(
        manager=manager,
        decomposer=decomposer,  # type: ignore[arg-type]
        critic_runner=FakeCriticRunner(_make_approve_verdict()),  # type: ignore[arg-type]
        worktree_mgr=FakeWorktreeManager(tmp_path / "worktrees"),  # type: ignore[arg-type]
        env_builder=lambda p: {},
        budget=BudgetTracker(per_mission_usd=10.0, daily_usd=100.0),
        worker_factory=lambda step: FakeWorker(),
        job_factory=FakeJobObject,
        isolation_root=tmp_path / "missions",
    )


@pytest.mark.asyncio
async def test_cancel_running_mission_returns_false_when_idle(
    manager: MissionManager, tmp_path: Path
) -> None:
    """No in-flight task for this mission — cancel reports False."""
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(manager=manager, tmp_path=tmp_path, critic=critic)
    mid = await manager.dispatch(prompt="never started")
    assert k.cancel_running_mission(mid) is False


@pytest.mark.asyncio
async def test_cancel_running_mission_cancels_inflight_task(
    manager: MissionManager, tmp_path: Path
) -> None:
    """A running mission's task is cancelled and untracked afterwards."""
    decomposer = HangingDecomposer()
    k = _make_hanging_kontrollierer(
        manager=manager, tmp_path=tmp_path, decomposer=decomposer
    )
    mid = await manager.dispatch(prompt="hang forever")

    task = asyncio.create_task(k.run_mission(mid))
    await asyncio.wait_for(decomposer.entered.wait(), timeout=5.0)

    # Mirror the REST endpoint's protocol: terminal state first, then kill.
    await manager.transition_state(
        mid, MissionState.CANCELLED, reason="ui_cancel", source_actor="ui"
    )
    assert k.cancel_running_mission(mid) is True

    await asyncio.wait([task], timeout=5.0)
    assert task.done(), "run_mission task must end after cancellation"
    assert task.cancelled(), (
        "run_mission must end cancelled — swallowing the CancelledError "
        "breaks Python 3.11 cancel-scope semantics"
    )

    view = await manager.mission(mid)
    assert view is not None
    assert view.state == MissionState.CANCELLED
    # Tracking map is cleaned — a second cancel is a no-op.
    assert k.cancel_running_mission(mid) is False


@pytest.mark.asyncio
async def test_tracking_cleared_after_normal_completion(
    manager: MissionManager, tmp_path: Path
) -> None:
    """After a normal run the mission is untracked — cancel is a no-op."""
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(manager=manager, tmp_path=tmp_path, critic=critic)
    mid = await manager.dispatch(prompt="quick run")
    end_state = await k.run_mission(mid)
    assert end_state == MissionState.APPROVED
    assert k.cancel_running_mission(mid) is False
