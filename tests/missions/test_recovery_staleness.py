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
    WorkerDraftReady,
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


async def test_recovery_uses_interrupted_reason_when_draft_was_delivered(
    open_store: MissionManager,
) -> None:
    """A stale mission that had a WorkerDraftReady with real content must be swept
    with reason='interrupted' and partial_artifacts populated — not discarded as a
    bare 'crash_recovery' with empty artifacts (the false-negative delivery bug)."""
    mid = await _running_mission(open_store, "interrupted-with-draft")
    # Simulate the worker having delivered a real draft before the crash.
    await open_store.store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="worker",
            ts_ms=now_ms(),
            payload=WorkerDraftReady(
                worker_id="w1",
                artifact_uri="file:///out/report.html",
                diff="diff --git a/report.html b/report.html\n+<html>",
                tokens_used=10,
                cost_usd=0.0,
                session_id="s1",
            ),
        )
    )

    # Boot 40 minutes later — the mission is stale.
    future_now = now_ms() + 40 * _MIN_MS
    recovered = await startup_recover(open_store.store, now=future_now)

    assert mid in recovered, "interrupted mission must appear in the recovered list"
    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.FAILED

    events = await open_store.store.events_for_mission(mid)
    fail_events = [e for e in events if e.payload.event_type == "MissionFailed"]
    assert fail_events, "a MissionFailed event must have been appended"
    fail_payload = fail_events[-1].payload
    assert fail_payload.reason == "interrupted", (  # type: ignore[attr-defined]
        f"expected reason='interrupted', got {fail_payload.reason!r}"  # type: ignore[attr-defined]
    )
    assert "file:///out/report.html" in fail_payload.partial_artifacts, (  # type: ignore[attr-defined]
        f"artifact_uri must be preserved in partial_artifacts, got "
        f"{fail_payload.partial_artifacts!r}"  # type: ignore[attr-defined]
    )


async def test_recovery_uses_crash_recovery_reason_when_no_draft(
    open_store: MissionManager,
) -> None:
    """A stale mission with no WorkerDraftReady (or only an empty draft) must still
    be swept with reason='crash_recovery' and empty partial_artifacts."""
    mid = await _running_mission(open_store, "empty-crash-no-draft")

    future_now = now_ms() + 40 * _MIN_MS
    recovered = await startup_recover(open_store.store, now=future_now)

    assert mid in recovered
    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.FAILED

    events = await open_store.store.events_for_mission(mid)
    fail_events = [e for e in events if e.payload.event_type == "MissionFailed"]
    assert fail_events, "a MissionFailed event must have been appended"
    fail_payload = fail_events[-1].payload
    assert fail_payload.reason == "crash_recovery", (  # type: ignore[attr-defined]
        f"expected reason='crash_recovery', got {fail_payload.reason!r}"  # type: ignore[attr-defined]
    )
    assert fail_payload.partial_artifacts == [], (  # type: ignore[attr-defined]
        f"no draft => partial_artifacts must be empty, got "
        f"{fail_payload.partial_artifacts!r}"  # type: ignore[attr-defined]
    )


async def test_draft_with_empty_uri_but_diff_is_still_interrupted(
    open_store: MissionManager,
) -> None:
    """A WorkerDraftReady with an empty artifact_uri but a non-empty diff still
    represents real work — the recovery must use reason='interrupted' and include
    a synthetic 'draft:<mission_id>' entry in partial_artifacts, not discard the
    work as a bare crash_recovery."""
    mid = await _running_mission(open_store, "diff-only-draft")
    # Worker wrote a diff but did not produce an output URI (e.g. in-place edit).
    await open_store.store.append_and_publish(
        EventEnvelope(
            mission_id=mid,
            source_actor="worker",
            ts_ms=now_ms(),
            payload=WorkerDraftReady(
                worker_id="w2",
                artifact_uri="",  # no URI — only a diff
                diff="diff --git a/foo.py b/foo.py\n+x = 1",
                tokens_used=5,
                cost_usd=0.0,
                session_id="s2",
            ),
        )
    )

    future_now = now_ms() + 40 * _MIN_MS
    recovered = await startup_recover(open_store.store, now=future_now)

    assert mid in recovered, "diff-only draft mission must appear in the recovered list"
    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.FAILED

    events = await open_store.store.events_for_mission(mid)
    fail_events = [e for e in events if e.payload.event_type == "MissionFailed"]
    assert fail_events, "a MissionFailed event must have been appended"
    fail_payload = fail_events[-1].payload
    assert fail_payload.reason == "interrupted", (  # type: ignore[attr-defined]
        f"diff-only draft must yield reason='interrupted', got {fail_payload.reason!r}"  # type: ignore[attr-defined]
    )
    # The sentinel is now per-worker (draft:<worker_id>), not per-mission.
    # The WorkerDraftReady above uses worker_id="w2".
    expected_entry = "draft:w2"
    assert expected_entry in fail_payload.partial_artifacts, (  # type: ignore[attr-defined]
        f"expected 'draft:w2' in partial_artifacts, got "
        f"{fail_payload.partial_artifacts!r}"  # type: ignore[attr-defined]
    )


async def test_fresh_heartbeat_protects_silent_worker(open_store: MissionManager) -> None:
    """A mission whose last EVENT is older than stale_after_ms but whose
    heartbeat is recent must NOT be swept — a live orchestrator is still
    draining a busy-but-silent worker (Opus / long tool calls / CU)."""
    mid = await _running_mission(open_store, "busy-but-silent-worker")

    # Simulate: last event was 40 min ago (stale by event-ts alone).
    stale_event_ts = now_ms() - 40 * _MIN_MS

    # Manually insert an older event timestamp by touching the store row.
    # We use the store's connection directly to backdate the existing event.
    await open_store.store.conn.execute(
        "UPDATE mission_events SET ts_ms = ? WHERE mission_id = ?",
        (stale_event_ts, mid),
    )

    # But a fresh heartbeat was written (e.g. 5 s ago) — the worker is alive.
    fresh_hb_ts = now_ms() - 5_000
    await open_store.store.touch_heartbeat(mid, fresh_hb_ts)

    recovered = await startup_recover(open_store.store)  # default 30-min threshold

    assert recovered == [], (
        "a mission with a fresh heartbeat must not be swept, even if last event is stale"
    )
    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.RUNNING, (
        f"mission must remain RUNNING, got {view.state if view else None}"
    )


async def test_stale_event_and_no_heartbeat_is_swept(open_store: MissionManager) -> None:
    """A mission whose last event AND heartbeat are both older than stale_after_ms
    is a genuine orphan and must be swept to FAILED('crash_recovery')."""
    mid = await _running_mission(open_store, "orphan-no-heartbeat")

    # Boot 40 minutes later: both last event and heartbeat (default 0) are stale.
    future_now = now_ms() + 40 * _MIN_MS
    recovered = await startup_recover(open_store.store, now=future_now)

    assert mid in recovered, "orphaned mission with stale event and no heartbeat must be swept"
    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.FAILED


async def test_delivered_artifacts_dedupes_multi_worker_diff_only(
    open_store: MissionManager,
) -> None:
    """A multi-step mission with TWO diff-only workers (different worker_ids, no
    artifact_uri) must produce exactly 2 distinct sentinel entries in
    partial_artifacts — one per worker — with no duplicates, and the mission
    must be swept with reason='interrupted'.

    Regression guard for MAJOR-2: the old implementation emitted
    ``f"draft:{mission_id}"`` for every diff-only draft, so two workers on the
    same mission produced identical duplicates in partial_artifacts.
    """
    mid = await _running_mission(open_store, "multi-worker-diff-only")

    # Two separate workers both produce a diff but no artifact_uri.
    for wid in ("w1", "w2"):
        await open_store.store.append_and_publish(
            EventEnvelope(
                mission_id=mid,
                source_actor="worker",
                ts_ms=now_ms(),
                payload=WorkerDraftReady(
                    worker_id=wid,
                    artifact_uri="",
                    diff=f"diff --git a/{wid}.py b/{wid}.py\n+x = 1",
                    tokens_used=5,
                    cost_usd=0.0,
                    session_id=f"s-{wid}",
                ),
            )
        )

    future_now = now_ms() + 40 * _MIN_MS
    recovered = await startup_recover(open_store.store, now=future_now)

    assert mid in recovered, "multi-worker diff-only mission must appear in the recovered list"
    view = await open_store.mission(mid)
    assert view is not None and view.state == MissionState.FAILED

    events = await open_store.store.events_for_mission(mid)
    fail_events = [e for e in events if e.payload.event_type == "MissionFailed"]
    assert fail_events, "a MissionFailed event must have been appended"
    fail_payload = fail_events[-1].payload

    assert fail_payload.reason == "interrupted", (  # type: ignore[attr-defined]
        f"multi-worker diff-only mission must yield reason='interrupted', "
        f"got {fail_payload.reason!r}"  # type: ignore[attr-defined]
    )
    partial = fail_payload.partial_artifacts  # type: ignore[attr-defined]
    assert partial == ["draft:w1", "draft:w2"], (
        f"expected exactly ['draft:w1', 'draft:w2'] (per-worker, no duplicates), "
        f"got {partial!r}"
    )
