"""Activity-aware + terminal-reconcile crash recovery.

Root cause of the "Mission failed although it worked, ~1h later, randomly"
report (live forensic 2026-05-31, missions 019e6fea / 019e7095): a SECOND
Jarvis instance (e.g. a `--headless` launch that never sets
JARVIS_PRIMARY_INSTANCE, so server.py defaults it to primary) runs
`startup_recover` against the shared `missions.db` and sweeps the FIRST
(live) instance's ACTIVELY RUNNING missions to FAILED('crash_recovery').
Mission 019e6fea was marked crash_recovery 39 s after its iter-1 WorkerSpawned,
then ran on to MissionApproved 11 min later — the header stayed poisoned at
FAILED.

The robust fix makes `startup_recover` activity-aware: a mission whose last
event is recent (< stale_after_ms) is assumed owned by a live orchestrator and
is SKIPPED, never swept. And a non-terminal mission whose event log already
carries a terminal event (e.g. MissionApproved) is RECONCILED to that real
state instead of being failed — which also repairs already-poisoned missions
on the next boot.
"""
from __future__ import annotations

from pathlib import Path

import pytest_asyncio

from jarvis.missions.events import (
    EventEnvelope,
    MissionApproved,
    now_ms,
)
from jarvis.missions.manager import MissionManager
from jarvis.missions.recovery import startup_recover
from jarvis.missions.state_machine import MissionState


_MIN_MS = 60_000


@pytest_asyncio.fixture
async def open_store(tmp_missions_db: Path):
    """A started store on a fresh DB (recovery NOT auto-run)."""
    m = MissionManager(tmp_missions_db)
    await m.start(recover=False)  # open store without sweeping
    try:
        yield m
    finally:
        await m.stop()


async def _running_mission(m: MissionManager, prompt: str = "task") -> str:
    mid = await m.dispatch(prompt=prompt)
    await m.transition_state(m_id := mid, MissionState.RUNNING, reason="worker-spawn")
    return m_id


async def test_recovery_skips_recently_active_mission(open_store: MissionManager) -> None:
    """A mission with a fresh last event is being run by a LIVE instance — it
    must NOT be swept to crash_recovery (the smoking-gun 019e6fea defect)."""
    mid = await _running_mission(open_store, "live-and-running")

    recovered = await startup_recover(open_store.store)  # default 30-min threshold

    assert recovered == [], "a recently-active mission must not be recovered"
    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.RUNNING, (
        f"active mission must stay RUNNING, got {view.state if view else None}"
    )


async def test_recovery_sweeps_genuinely_stale_mission(open_store: MissionManager) -> None:
    """A mission with no activity for longer than the staleness window is a
    genuine orphan from a crashed run — recover it as before."""
    mid = await _running_mission(open_store, "orphaned-crash")

    # Pretend we boot 40 minutes later: the last event is now stale.
    future_now = now_ms() + 40 * _MIN_MS
    recovered = await startup_recover(open_store.store, now=future_now)

    assert mid in recovered
    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.FAILED


async def test_recovery_reconciles_unfinalized_approved_mission(
    open_store: MissionManager,
) -> None:
    """If the orchestrator crashed AFTER publishing MissionApproved but BEFORE
    updating the header, the header is non-terminal yet the work succeeded.
    Recovery must reconcile to APPROVED — never report a successful mission as
    failed (the user's 'failed although it worked' complaint)."""
    mid = await open_store.dispatch(prompt="approved-but-header-lagged")
    await open_store.transition_state(mid, MissionState.RUNNING, reason="r")
    await open_store.transition_state(mid, MissionState.CRITIQUING, reason="c")
    # Append a terminal MissionApproved EVENT but deliberately do NOT upsert the
    # header to APPROVED (simulate the crash window).
    await open_store.store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionApproved(
                result_uri="diff://x",
                tokens_used=10,
                cost_usd=0.1,
                wall_ms=1000,
                summary_de="Fertig.",
                summary_en="Done.",
            ),
        )
    )

    # Even far in the future (well past staleness), reconcile wins over sweep.
    recovered = await startup_recover(open_store.store, now=now_ms() + 99 * _MIN_MS)

    assert mid not in recovered, "a succeeded mission must not be in the failed list"
    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.APPROVED, (
        f"unfinalized-approved mission must reconcile to APPROVED, got "
        f"{view.state if view else None}"
    )
    # And no fresh crash_recovery MissionFailed event was appended.
    events = await open_store.store.events_for_mission(mid)
    fail_reasons = [
        e.payload.reason  # type: ignore[attr-defined]
        for e in events
        if e.payload.event_type == "MissionFailed"
    ]
    assert "crash_recovery" not in fail_reasons
