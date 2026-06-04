"""Tests fuer MissionManager Lifecycle + Recovery (Crash-Restart-Szenario)."""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from jarvis.missions.event_bus import MissionBus
from jarvis.missions.events import (
    EventEnvelope,
    MissionDispatched,
    MissionFailed,
    MissionStateChanged,
    now_ms,
)
from jarvis.missions.manager import MissionManager, MissionView
from jarvis.missions.state_machine import IllegalStateTransition, MissionState


# --- Fixtures ---


@pytest_asyncio.fixture
async def manager(tmp_missions_db: Path):
    m = MissionManager(tmp_missions_db)
    await m.start()
    try:
        yield m
    finally:
        await m.stop()


# --- Lifecycle ---


async def test_start_is_idempotent(tmp_missions_db: Path) -> None:
    m = MissionManager(tmp_missions_db)
    await m.start()
    second = await m.start()
    assert second == []
    await m.stop()


async def test_dispatch_creates_pending_mission(manager: MissionManager) -> None:
    mid = await manager.dispatch(prompt="aufgabe x")
    view = await manager.mission(mid)
    assert isinstance(view, MissionView)
    assert view.state == MissionState.PENDING
    assert view.prompt == "aufgabe x"


async def test_dispatch_emits_to_bus(manager: MissionManager) -> None:
    received: list[EventEnvelope] = []

    async def collect(e: EventEnvelope) -> None:
        received.append(e)

    manager.bus.subscribe_all(collect)
    mid = await manager.dispatch(prompt="hello", language="de")
    assert len(received) == 1
    assert received[0].mission_id == mid
    assert isinstance(received[0].payload, MissionDispatched)
    assert received[0].payload.prompt == "hello"


async def test_dispatch_persists_event(manager: MissionManager) -> None:
    mid = await manager.dispatch(prompt="persist-me")
    events = await manager.store.events_for_mission(mid)
    assert len(events) == 1
    assert isinstance(events[0].payload, MissionDispatched)


# --- Transitions ---


async def test_transition_legal_path(manager: MissionManager) -> None:
    mid = await manager.dispatch(prompt="t")
    await manager.transition_state(mid, MissionState.RUNNING, reason="worker-spawn")
    await manager.transition_state(mid, MissionState.CRITIQUING, reason="diff-ready")
    await manager.transition_state(mid, MissionState.APPROVED, reason="critic-approved")
    view = await manager.mission(mid)
    assert view is not None
    assert view.state == MissionState.APPROVED


async def test_transition_emits_state_change_event(manager: MissionManager) -> None:
    mid = await manager.dispatch(prompt="t")
    env = await manager.transition_state(
        mid, MissionState.RUNNING, reason="dispatch"
    )
    assert isinstance(env.payload, MissionStateChanged)
    assert env.payload.from_state == "PENDING"
    assert env.payload.to_state == "RUNNING"
    assert env.payload.reason == "dispatch"


async def test_transition_illegal_raises(manager: MissionManager) -> None:
    mid = await manager.dispatch(prompt="t")
    with pytest.raises(IllegalStateTransition):
        await manager.transition_state(
            mid, MissionState.APPROVED, reason="skip-ahead"
        )


async def test_transition_unknown_mission_raises(manager: MissionManager) -> None:
    with pytest.raises(KeyError, match="nicht gefunden"):
        await manager.transition_state(
            "00000000-0000-0000-0000-000000000000",
            MissionState.RUNNING,
            reason="ghost",
        )


async def test_mission_unknown_returns_none(manager: MissionManager) -> None:
    view = await manager.mission("00000000-0000-0000-0000-000000000000")
    assert view is None


async def test_dispatch_before_start_raises(tmp_missions_db: Path) -> None:
    m = MissionManager(tmp_missions_db)
    with pytest.raises(RuntimeError, match="start"):
        await m.dispatch(prompt="too-early")


# --- Recovery ---


async def test_recovery_marks_running_as_failed(tmp_missions_db: Path) -> None:
    """Crash-Simulation: dispatchen + auf RUNNING + manager.stop() ohne sauberen Endzustand."""
    m1 = MissionManager(tmp_missions_db)
    await m1.start()
    mid = await m1.dispatch(prompt="will-crash")
    await m1.transition_state(mid, MissionState.RUNNING, reason="worker-spawn")
    # "Crash": stop ohne zu Endzustand zu transitionieren
    await m1.stop()

    # Restart auf gleicher DB. stale_after_ms=0 models a genuine crash where
    # the mission is truly orphaned (no live instance) — every non-terminal
    # mission is immediately stale and must be swept. The default 30-min
    # window (which protects missions a live instance is still running) is
    # covered by tests/missions/test_recovery_staleness.py.
    m2 = MissionManager(tmp_missions_db)
    recovered = await m2.start(stale_after_ms=0)
    try:
        assert mid in recovered
        view = await m2.mission(mid)
        assert view is not None
        assert view.state == MissionState.FAILED
    finally:
        await m2.stop()


async def test_recovery_emits_two_events_per_mission(tmp_missions_db: Path) -> None:
    """Recovery emittiert MissionStateChanged + MissionFailed in dieser Reihenfolge."""
    m1 = MissionManager(tmp_missions_db)
    await m1.start()
    mid = await m1.dispatch(prompt="x")
    await m1.transition_state(mid, MissionState.RUNNING, reason="r")
    # Pre-Recovery: 2 Events (MissionDispatched + MissionStateChanged PENDING->RUNNING)
    pre = await m1.store.events_for_mission(mid)
    assert len(pre) == 2
    await m1.stop()

    m2 = MissionManager(tmp_missions_db)
    received: list[EventEnvelope] = []

    async def collect(e: EventEnvelope) -> None:
        received.append(e)

    m2.bus.subscribe_all(collect)
    await m2.start(stale_after_ms=0)  # orphaned-crash model: sweep immediately
    try:
        # Recovery hat 2 weitere Events emittiert (StateChange + Failed)
        # Aber: collect war erst NACH start() registriert — deshalb pruefen wir die DB
        post = await m2.store.events_for_mission(mid)
        assert len(post) == 4
        types = [e.payload.event_type for e in post]
        assert types == [
            "MissionDispatched",
            "MissionStateChanged",  # PENDING -> RUNNING
            "MissionStateChanged",  # RUNNING -> FAILED (recovery)
            "MissionFailed",
        ]
        # letztes State-Change-Event referenziert RUNNING -> FAILED
        sc = post[2].payload
        assert isinstance(sc, MissionStateChanged)
        assert sc.from_state == "RUNNING"
        assert sc.to_state == "FAILED"
        assert sc.reason == "crash_recovery"
        # MissionFailed traegt den last_state
        mf = post[3].payload
        assert isinstance(mf, MissionFailed)
        assert mf.last_state == "RUNNING"
        assert mf.reason == "crash_recovery"
    finally:
        await m2.stop()


async def test_recovery_publishes_to_bus(tmp_missions_db: Path) -> None:
    """Recovery-Events landen auf dem Bus eines neu erstellten MissionManager."""
    m1 = MissionManager(tmp_missions_db)
    await m1.start()
    mid = await m1.dispatch(prompt="x")
    await m1.transition_state(mid, MissionState.RUNNING, reason="r")
    await m1.stop()

    bus = MissionBus()
    received: list[EventEnvelope] = []

    async def collect(e: EventEnvelope) -> None:
        received.append(e)

    bus.subscribe_all(collect)
    m2 = MissionManager(tmp_missions_db, bus=bus)
    await m2.start(stale_after_ms=0)  # orphaned-crash model: sweep immediately
    try:
        # Beim start wurden 2 Recovery-Events publiziert
        types = [e.payload.event_type for e in received]
        assert "MissionStateChanged" in types
        assert "MissionFailed" in types
    finally:
        await m2.stop()


async def test_recovery_idempotent_on_terminal_states(tmp_missions_db: Path) -> None:
    """Wenn alle Missions terminal sind, recovered nichts."""
    m1 = MissionManager(tmp_missions_db)
    await m1.start()
    mid = await m1.dispatch(prompt="x")
    await m1.transition_state(mid, MissionState.RUNNING, reason="r")
    await m1.transition_state(mid, MissionState.CRITIQUING, reason="c")
    await m1.transition_state(mid, MissionState.APPROVED, reason="a")
    await m1.stop()

    m2 = MissionManager(tmp_missions_db)
    recovered = await m2.start()
    try:
        assert recovered == []
    finally:
        await m2.stop()


async def test_recovery_handles_multiple_stale_missions(tmp_missions_db: Path) -> None:
    m1 = MissionManager(tmp_missions_db)
    await m1.start()
    mids = [await m1.dispatch(prompt=f"task-{i}") for i in range(3)]
    for mid in mids:
        await m1.transition_state(mid, MissionState.RUNNING, reason="r")
    await m1.stop()

    m2 = MissionManager(tmp_missions_db)
    recovered = await m2.start(stale_after_ms=0)  # orphaned-crash model
    try:
        assert sorted(recovered) == sorted(mids)
        for mid in mids:
            view = await m2.mission(mid)
            assert view is not None
            assert view.state == MissionState.FAILED
    finally:
        await m2.stop()


async def test_start_skips_recovery_when_recover_false(
    tmp_missions_db: Path,
) -> None:
    """Fix #2 (2026-05-29): a secondary/dev instance (--no-lock) must NOT run
    the crash_recovery sweep, else its boot marks the PRIMARY instance's
    in-flight missions as FAILED('crash_recovery') — killing live work
    (mission 019e7095 / 019e6fea died exactly this way). With recover=False
    the sweep is skipped and the running mission is left untouched."""
    m1 = MissionManager(tmp_missions_db)
    await m1.start()
    mid = await m1.dispatch(prompt="primary-mission-still-running")
    await m1.transition_state(mid, MissionState.RUNNING, reason="worker-spawn")
    await m1.stop()  # store closed, but the mission is non-terminal (RUNNING)

    # Secondary instance boots on the same DB but is NOT the primary.
    m2 = MissionManager(tmp_missions_db)
    recovered = await m2.start(recover=False)
    try:
        assert recovered == [], "secondary must not recover/sweep anything"
        view = await m2.mission(mid)
        assert view is not None
        assert view.state == MissionState.RUNNING, (
            "the primary's running mission must NOT be marked FAILED by a "
            f"secondary's boot, got {view.state}"
        )
    finally:
        await m2.stop()


async def test_recovery_preserves_pending_mission_as_failed(
    tmp_missions_db: Path,
) -> None:
    """Eine niemals zu RUNNING transitionierte Mission gilt auch als stale."""
    m1 = MissionManager(tmp_missions_db)
    await m1.start()
    mid = await m1.dispatch(prompt="never-ran")
    # State bleibt PENDING
    await m1.stop()

    m2 = MissionManager(tmp_missions_db)
    recovered = await m2.start(stale_after_ms=0)  # orphaned-crash model
    try:
        assert mid in recovered
        view = await m2.mission(mid)
        assert view is not None
        assert view.state == MissionState.FAILED
    finally:
        await m2.stop()
