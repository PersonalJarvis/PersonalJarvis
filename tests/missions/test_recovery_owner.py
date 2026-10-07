"""Owner-aware crash recovery (live forensic 2026-10-02, mission 01a0fcc3).

An artifact mission's worker was killed when the desktop app restarted at
15:19:40, 85 s after dispatch. The new instance's recovery sweep saw a heartbeat
younger than 30 minutes and "presumed" a live instance still owned the mission,
so it stayed RUNNING/CRITIQUING — with no worker behind it — until 15:56:42,
when it was finally swept to FAILED('crash_recovery') with no artifact.

The fix: the orchestrator stamps its process identity with each heartbeat, and
recovery sweeps a mission at once when that owner process is provably dead,
while an alive or unknown owner keeps the conservative freshness guard.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest_asyncio

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.missions.events import now_ms
from jarvis.missions.manager import MissionManager
from jarvis.missions.ownership import current_process_identity, owner_is_alive
from jarvis.missions.recovery import startup_recover
from jarvis.missions.state_machine import MissionState


@pytest_asyncio.fixture
async def open_store(tmp_missions_db: Path):
    m = MissionManager(tmp_missions_db)
    await m.start(recover=False)
    try:
        yield m
    finally:
        await m.stop()


async def _critiquing_mission_owned_by(
    m: MissionManager, owner_pid: int, owner_start_ms: int
) -> str:
    """A mission mid-run whose heartbeat is 5 s old, stamped by ``owner_pid``."""
    mid = await m.dispatch(prompt="artifact build")
    await m.transition_state(mid, MissionState.RUNNING, reason="kontrollierer-start")
    await m.transition_state(mid, MissionState.CRITIQUING, reason="iter-0-start")
    await m.store.touch_heartbeat(mid, now_ms() - 5_000)
    await m.store.conn.execute(
        "UPDATE missions SET owner_pid = ?, owner_start_ms = ? WHERE id = ?",
        (owner_pid, owner_start_ms, mid),
    )
    return mid


def _exited_pid() -> int:
    """PID of a real process that has already exited."""
    proc = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        check=True,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    return int(proc.stdout.strip())


def _dead_owner(pid: int, start_ms: int) -> bool | None:
    return False


def _alive_owner(pid: int, start_ms: int) -> bool | None:
    return True


def _unknown_owner(pid: int, start_ms: int) -> bool | None:
    return None


async def test_dead_owner_is_swept_despite_fresh_heartbeat(
    open_store: MissionManager,
) -> None:
    """The 01a0fcc3 case: fresh heartbeat, but the process that wrote it died
    in a restart. Recovery must fail the mission now, not 30+ minutes later."""
    mid = await _critiquing_mission_owned_by(open_store, 424242, 1_790_947_000_000)

    recovered = await startup_recover(open_store.store, owner_alive_fn=_dead_owner)

    assert recovered == [mid]
    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.FAILED
    events = await open_store.store.events_for_mission(mid)
    failed = [e.payload for e in events if e.payload.event_type == "MissionFailed"]
    assert len(failed) == 1
    assert failed[0].reason == "crash_recovery"
    assert failed[0].last_state == MissionState.CRITIQUING.value
    assert failed[0].error_detail and "424242" in failed[0].error_detail


async def test_alive_owner_keeps_the_freshness_guard(open_store: MissionManager) -> None:
    """A second instance sharing missions.db must never sweep a mission a live
    first instance is running (the 019e6fea false-FAILED bug stays fixed)."""
    mid = await _critiquing_mission_owned_by(open_store, 424242, 1_790_947_000_000)

    recovered = await startup_recover(open_store.store, owner_alive_fn=_alive_owner)

    assert recovered == []
    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.CRITIQUING


async def test_unknown_owner_keeps_the_freshness_guard(open_store: MissionManager) -> None:
    """No stamp, no psutil, access denied: never sweep on a guess."""
    mid = await _critiquing_mission_owned_by(open_store, 0, 0)

    recovered = await startup_recover(open_store.store, owner_alive_fn=_unknown_owner)

    assert recovered == []
    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.CRITIQUING


async def test_heartbeat_stamps_this_process_as_owner(open_store: MissionManager) -> None:
    mid = await open_store.dispatch(prompt="artifact build")

    assert await open_store.store.get_owner(mid) == (0, 0)
    await open_store.store.touch_heartbeat(mid, now_ms())

    assert await open_store.store.get_owner(mid) == current_process_identity()
    assert await open_store.store.get_owner("no-such-mission") == (0, 0)


async def test_mission_heartbeat_by_this_live_process_is_not_swept(
    open_store: MissionManager,
) -> None:
    """End to end with the real liveness probe: the periodic re-sweep runs in
    the same process that owns the mission and must leave it alone."""
    mid = await open_store.dispatch(prompt="artifact build")
    await open_store.transition_state(mid, MissionState.RUNNING, reason="r")
    await open_store.store.touch_heartbeat(mid, now_ms())

    assert await startup_recover(open_store.store) == []


async def test_mission_owned_by_an_exited_process_is_swept(
    open_store: MissionManager,
) -> None:
    """End to end with the real liveness probe and a real process that has
    exited — the restart that killed mission 01a0fcc3's owner."""
    mid = await _critiquing_mission_owned_by(open_store, _exited_pid(), now_ms() - 60_000)

    recovered = await startup_recover(open_store.store)

    assert recovered == [mid]


def test_owner_is_alive_probe() -> None:
    pid, start_ms = current_process_identity()
    assert pid == os.getpid()
    assert owner_is_alive(pid, start_ms) is True
    assert owner_is_alive(0, 0) is None
    if start_ms:
        # Same pid, different start time: the pid was reused by a new process.
        assert owner_is_alive(pid, start_ms - 3_600_000) is False
