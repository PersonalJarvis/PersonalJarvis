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


@pytest.mark.asyncio
async def test_running_mission_ids_lists_only_inflight_missions(
    manager: MissionManager, tmp_path: Path
) -> None:
    """``running_mission_ids`` exposes exactly the in-flight runs, no zombies.

    The restart guard (POST /api/settings/restart-app) reads this to refuse a
    silent kill of live missions. It must report a mission only while its
    ``run_mission`` task is actually pending: empty when idle, the id while the
    worker runs, and empty again after the task finishes or is cancelled — so a
    finished mission never spuriously blocks a restart.
    """
    decomposer = HangingDecomposer()
    k = _make_hanging_kontrollierer(
        manager=manager, tmp_path=tmp_path, decomposer=decomposer
    )
    # Idle: nothing in flight.
    assert k.running_mission_ids() == []

    mid = await manager.dispatch(prompt="hang forever")
    task = asyncio.create_task(k.run_mission(mid))
    await asyncio.wait_for(decomposer.entered.wait(), timeout=5.0)

    # In flight: reported.
    assert k.running_mission_ids() == [mid]

    # After teardown: gone (no stale entry blocks a restart).
    await k.cancel_all_running(reason="app_shutdown")
    await asyncio.wait([task], timeout=5.0)
    assert k.running_mission_ids() == []


@pytest.mark.asyncio
async def test_cancel_all_running_finalizes_inflight_missions(
    manager: MissionManager, tmp_path: Path
) -> None:
    """App shutdown finalizes every in-flight mission as CANCELLED.

    Live incident 2026-06-10 19:24:12 (missions 019eb27f + 019eb288): the
    app's self-restart killed the process with two missions in flight;
    nothing finalized them, so they lingered non-terminal until the
    recovery re-sweep buried them 30 minutes later as opaque
    crash_recovery / ERROR cards with zero artifacts. The shutdown path
    must flip each tracked mission to a terminal CANCELLED immediately
    (honest UI card with a real reason), then cancel the run task.
    """
    decomposer = HangingDecomposer()
    k = _make_hanging_kontrollierer(
        manager=manager, tmp_path=tmp_path, decomposer=decomposer
    )
    mid = await manager.dispatch(prompt="hang forever")
    task = asyncio.create_task(k.run_mission(mid))
    await asyncio.wait_for(decomposer.entered.wait(), timeout=5.0)

    finalized = await k.cancel_all_running(reason="app_shutdown")

    assert finalized == [mid]
    await asyncio.wait([task], timeout=5.0)
    assert task.done(), "run_mission task must end after shutdown cancel"
    view = await manager.mission(mid)
    assert view is not None
    assert view.state == MissionState.CANCELLED

    # Idempotent: nothing left in flight.
    assert await k.cancel_all_running(reason="app_shutdown") == []


@pytest.mark.asyncio
async def test_cancel_cannot_be_followed_by_stale_state_transition(
    manager: MissionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale in-flight transition must not append after CANCELLED.

    Live DB evidence (2026-06-15, mission 019ecbb2-c9ff): shutdown wrote
    RUNNING -> CANCELLED, then a concurrently running iteration appended
    RUNNING -> CRITIQUING afterward. That leaves the UI showing active work
    for an already terminal mission.
    """
    mid = await manager.dispatch(prompt="race")
    await manager.transition_state(
        mid, MissionState.RUNNING, reason="start", source_actor="kontrollierer"
    )

    original_append = manager.store.append_and_publish
    stale_transition_ready = asyncio.Event()
    release_stale_transition = asyncio.Event()

    async def _gated_append(envelope):
        payload = envelope.payload
        if (
            payload.event_type == "MissionStateChanged"
            and payload.to_state == MissionState.CRITIQUING.value
        ):
            stale_transition_ready.set()
            await release_stale_transition.wait()
        return await original_append(envelope)

    monkeypatch.setattr(manager.store, "append_and_publish", _gated_append)

    stale_transition = asyncio.create_task(
        manager.transition_state(
            mid,
            MissionState.CRITIQUING,
            reason="iter-0-start",
            source_actor="kontrollierer",
        )
    )
    await asyncio.wait_for(stale_transition_ready.wait(), timeout=5.0)

    cancel = asyncio.create_task(
        manager.transition_state(
            mid,
            MissionState.CANCELLED,
            reason="app_shutdown",
            source_actor="kontrollierer",
        )
    )
    await asyncio.sleep(0.05)
    release_stale_transition.set()
    await asyncio.wait_for(asyncio.gather(stale_transition, cancel), timeout=5.0)

    state_changes = [
        e.payload
        for e in await manager.store.events_for_mission(mid)
        if e.payload.event_type == "MissionStateChanged"
    ]
    seen_cancel = False
    for payload in state_changes:
        if seen_cancel:
            pytest.fail(
                "state transition appended after CANCELLED: "
                f"{payload.from_state}->{payload.to_state} ({payload.reason})"
            )
        if payload.to_state == MissionState.CANCELLED.value:
            seen_cancel = True

    assert seen_cancel, "test setup must produce a CANCELLED transition"
    view = await manager.mission(mid)
    assert view is not None
    assert view.state == MissionState.CANCELLED
