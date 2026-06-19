"""Periodic recovery re-sweep (post-boot orphan finalization).

``startup_recover`` runs once at boot. Thanks to the active-guard it correctly
SKIPS a mission whose last event/heartbeat is younger than the staleness window
(a parallel ``--no-lock`` instance may genuinely own it). But that guard is
boot-only: a mission whose owning instance dies *after* boot is never re-checked
and lingers non-terminal (e.g. CRITIQUING) in the DB and UI forever ("missions
never find an end" — live forensic 2026-06-10, mission 019eb25c).

``periodic_recovery_sweep`` closes that gap by re-running the SAME conservative,
active-guarded sweep on a timer: once an orphan's last activity crosses the
unchanged staleness threshold, the next tick finalizes it. These tests prove:

1. a non-terminal mission with a fresh heartbeat is NOT touched by a sweep tick;
2. after the clock advances past the threshold, the next tick finalizes it;
3. a tick whose ``startup_recover`` raises does not kill the loop;
4. cancellation stops the loop cleanly.

The tests drive the loop with a tiny ``interval_s`` and an injected clock — no
real 10-minute sleeps — so the whole module runs in well under a second.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from jarvis.missions.manager import MissionManager
from jarvis.missions.recovery import (
    RECOVERY_RESWEEP_INTERVAL_S,
    RECOVERY_STALE_AFTER_MS,
    periodic_recovery_sweep,
)
from jarvis.missions.state_machine import MissionState

_MIN_MS = 60_000


@pytest_asyncio.fixture
async def open_store(tmp_missions_db: Path):
    """A started store on a fresh DB (recovery NOT auto-run)."""
    m = MissionManager(tmp_missions_db)
    await m.start(recover=False)
    try:
        yield m
    finally:
        await m.stop()


async def _running_mission(m: MissionManager, prompt: str = "task") -> str:
    mid = await m.dispatch(prompt=prompt)
    await m.transition_state(mid, MissionState.RUNNING, reason="worker-spawn")
    return mid


def test_default_interval_is_ten_minutes() -> None:
    """The re-sweep cadence is a module constant defaulting to 10 minutes — far
    shorter than the 30-minute staleness window so an orphan lives at most one
    extra interval past the threshold."""
    assert RECOVERY_RESWEEP_INTERVAL_S == 10 * 60


async def test_tick_does_not_touch_fresh_mission(open_store: MissionManager) -> None:
    """A non-terminal mission whose last activity is fresher than the staleness
    threshold must NOT be finalized by a sweep tick — a live instance owns it.

    Drive exactly one tick with a near-zero interval and the real ``now`` clock,
    then cancel; the mission must still be RUNNING.
    """
    mid = await _running_mission(open_store, "live-and-running")

    task = asyncio.create_task(
        periodic_recovery_sweep(
            open_store.store,
            interval_s=0,  # fire immediately, repeatedly
            stale_after_ms=RECOVERY_STALE_AFTER_MS,  # default 30-min window
        )
    )
    # Let a handful of ticks run, then stop.
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.RUNNING, (
        f"a fresh mission must stay RUNNING across ticks, got "
        f"{view.state if view else None}"
    )


async def test_next_tick_finalizes_mission_once_stale(open_store: MissionManager) -> None:
    """After the staleness threshold is crossed, the NEXT tick must finalize the
    orphan to FAILED — the core fix for "missions never find an end".

    We model the passage of time by injecting a clock (``now_fn``) that jumps 40
    minutes into the future, exactly as the boot staleness tests advance ``now``.
    The mission is created fresh, so with the real clock it would be skipped; the
    future clock makes its last event stale and the tick sweeps it.
    """
    mid = await _running_mission(open_store, "orphaned-after-boot")

    # A clock 40 minutes ahead of the mission's last event -> stale.
    from jarvis.missions.events import now_ms

    base = now_ms()

    def future_clock() -> int:
        return base + 40 * _MIN_MS

    task = asyncio.create_task(
        periodic_recovery_sweep(
            open_store.store,
            interval_s=0,
            stale_after_ms=RECOVERY_STALE_AFTER_MS,
            now_fn=future_clock,
        )
    )
    # Poll until the sweep has finalized the mission (bounded so the test can
    # never hang), then cancel.
    for _ in range(200):
        await asyncio.sleep(0.005)
        view = await open_store.mission(mid)
        if view is not None and view.state == MissionState.FAILED:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.FAILED, (
        f"a stale orphan must be finalized to FAILED by the re-sweep, got "
        f"{view.state if view else None}"
    )


async def test_raising_tick_does_not_kill_loop(
    open_store: MissionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tick whose ``startup_recover`` raises must be swallowed: the loop must
    survive and keep ticking (mirrors EventBus ``_safe_dispatch``, AP-18).

    We patch ``startup_recover`` to raise on the first call and succeed after,
    counting calls. If the loop died on the exception, ``calls`` would stay at 1.
    """
    import jarvis.missions.recovery as recovery_mod

    calls = {"n": 0}
    real_startup_recover = recovery_mod.startup_recover

    async def flaky_startup_recover(store, **kwargs):  # noqa: ANN001, ANN003
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated transient DB error on tick 1")
        return await real_startup_recover(store, **kwargs)

    monkeypatch.setattr(recovery_mod, "startup_recover", flaky_startup_recover)

    task = asyncio.create_task(
        recovery_mod.periodic_recovery_sweep(open_store.store, interval_s=0)
    )
    # Wait until at least 3 ticks have happened (proves the loop survived the
    # raising first tick), bounded so the test cannot hang.
    for _ in range(200):
        await asyncio.sleep(0.005)
        if calls["n"] >= 3:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls["n"] >= 3, (
        f"the loop must keep ticking after a raising tick, got {calls['n']} call(s)"
    )


async def test_cancellation_stops_loop_cleanly(open_store: MissionManager) -> None:
    """Cancelling the loop task must propagate ``CancelledError`` and stop it —
    the app-shutdown contract (the task is created with ``asyncio.create_task``
    and cancelled in the server's cleanup path)."""
    task = asyncio.create_task(
        periodic_recovery_sweep(open_store.store, interval_s=0.01)
    )
    await asyncio.sleep(0.02)
    assert not task.done(), "loop should still be running before cancellation"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled(), "cancelled loop task must report cancelled()"
