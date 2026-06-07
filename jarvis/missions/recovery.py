"""Startup recovery for the Phase-6 mission subsystem.

If the orchestrator crashed while missions were in non-terminal states
(PENDING, RUNNING, CRITIQUING, LOOPING), they must eventually be marked as
FAILED('crash_recovery'). Otherwise they hang in the DB forever and no
subscriber ever receives a terminal state.

Pattern from ADR-0009 §"Decision §4 + Risk #9": scan list_non_terminal_missions,
emit MissionStateChanged + MissionFailed per stale mission, persist + publish.
Idempotent — a second recovery iteration finds no more stale missions.

ACTIVITY-AWARE (live forensic 2026-05-31, missions 019e6fea / 019e7095):
the original sweep marked EVERY non-terminal mission FAILED unconditionally.
When a SECOND instance booted against the shared `missions.db` (e.g. a
`--headless` launch that never set JARVIS_PRIMARY_INSTANCE, so the recover-flag
guard defaulted it to primary), its sweep killed the FIRST instance's actively
running missions — which then ran on to MissionApproved, leaving a poisoned
FAILED header. That is the "Mission failed although it worked, ~1h later,
randomly" bug. Two guards make the sweep safe regardless of which instance runs
it (so it is robust to headless double-launch, autostart races, and the
cloud-first headless-VPS case alike):

1. RECONCILE — if a non-terminal mission's event log already carries a terminal
   event (MissionApproved/Failed/Cancelled/TimedOut), align the header to that
   real state. No new failure is emitted, so a succeeded-but-unfinalized mission
   is never reported as failed, and an already-poisoned mission is repaired.
2. ACTIVE-GUARD — a mission whose last event is younger than ``stale_after_ms``
   is presumed owned by a LIVE orchestrator and is SKIPPED. Only genuinely
   stale, orphaned missions are swept to FAILED('crash_recovery').
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from .event_store import MissionEventStore
from .events import (
    EventEnvelope,
    MissionApproved,
    MissionCancelled,
    MissionFailed,
    MissionStateChanged,
    MissionTimedOut,
    now_ms,
)
from .state_machine import MissionState

log = logging.getLogger(__name__)


# How long a mission may be quiet before recovery treats it as orphaned. A live
# worker run (claude-cli, up to its first-output/timeout window across several
# critic iterations) can be silent for many minutes, so the window is generous:
# the cost of waiting is a crashed mission lingering as RUNNING a little longer,
# whereas a too-short window re-introduces the false-FAILED bug by sweeping a
# mission another live instance is actively running.
RECOVERY_STALE_AFTER_MS: int = 30 * 60 * 1000  # 30 minutes


# Terminal event type -> header state to reconcile to.
_TERMINAL_EVENT_STATE: dict[str, str] = {
    "MissionApproved": MissionState.APPROVED.value,
    "MissionFailed": MissionState.FAILED.value,
    "MissionCancelled": MissionState.CANCELLED.value,
    "MissionTimedOut": MissionState.TIMED_OUT.value,
}

# Drift guard: the keys above are event_type discriminator strings that must
# match the payload classes. If a terminal payload is renamed or added without
# updating this map, recovery silently stops reconciling it — make that a loud
# import-time failure instead (this repo's BUG-008 enum-drift discipline).
assert set(_TERMINAL_EVENT_STATE) == {
    MissionApproved.model_fields["event_type"].default,
    MissionFailed.model_fields["event_type"].default,
    MissionCancelled.model_fields["event_type"].default,
    MissionTimedOut.model_fields["event_type"].default,
}, "_TERMINAL_EVENT_STATE drifted from the terminal mission-event payloads"


async def startup_recover(
    store: MissionEventStore,
    *,
    stale_after_ms: int = RECOVERY_STALE_AFTER_MS,
    now: int | None = None,
    now_fn: Callable[[], int] = now_ms,
) -> list[str]:
    """Recover genuinely-orphaned missions; never touch live or finished ones.

    For each non-terminal mission:
        1. If its event log carries a terminal event, reconcile the header to
           that state (no new event emitted) and skip the sweep.
        2. Else if its last event is younger than ``stale_after_ms``, skip it —
           a live orchestrator is presumed to own it.
        3. Else mark it FAILED('crash_recovery'), emitting (in order):
           MissionStateChanged(to=FAILED) then MissionFailed.

    Args:
        store: open MissionEventStore.
        stale_after_ms: quiet-time after which a non-terminal mission is
            considered orphaned. ``0`` restores the old unconditional sweep.
        now: wall-clock ms since the Unix epoch to compare event timestamps
            against (defaults to ``now_fn()``); injectable for tests. Note
            ``now=0`` means the epoch (1970), NOT "use the default".
        now_fn: clock function (default :func:`now_ms`).

    Returns:
        The list of mission_ids actually swept to FAILED (empty if nothing was
        orphaned — reconciled and skipped missions are NOT included).
    """
    now_ts = now if now is not None else now_fn()
    stale = await store.list_non_terminal_missions()
    recovered_ids: list[str] = []
    crash_ids: list[str] = []
    interrupted_ids: list[str] = []
    reconciled_ids: list[str] = []
    skipped_active: list[str] = []

    for mission_id, prompt, last_state in stale:
        events = await store.events_for_mission(mission_id)

        # 1. Reconcile: a terminal event was recorded but the header lagged
        #    (crash between publish and header upsert, or an earlier wrong
        #    crash_recovery that a later success overtook — mission 019e6fea).
        terminal_state = _last_terminal_state(events)
        if terminal_state is not None:
            # `language` is required by the signature but IGNORED on conflict:
            # upsert_mission's ON CONFLICT updates only state/updated_ms/
            # iteration/cost_usd, never language — so the row's real language is
            # preserved. (If that SQL ever changes, fetch the real language here.)
            await store.upsert_mission(
                mission_id=mission_id,
                prompt=prompt,
                state=terminal_state,
                language="de",
                ts_ms=now_ts,
            )
            reconciled_ids.append(mission_id)
            continue

        # 2. Active-guard: recent activity (last event OR a live heartbeat) →
        #    a live instance owns this mission. The heartbeat catches a worker
        #    that is busy but silent for >stale_after_ms between stream events
        #    (Opus / long tool calls / Computer-Use) — without it, a restart
        #    would sweep a mission another instance is actively running.
        #
        #    No events AND no heartbeat means the crash hit the dispatch window
        #    (header upserted, MissionDispatched never written): there is no
        #    activity to protect, so fall through to the sweep (do NOT treat
        #    eventless + heartbeat-zero as active — that would leave it stuck
        #    non-terminal forever).
        last_event_ts = events[-1].ts_ms if events else 0
        heartbeat_ts = await store.get_heartbeat(mission_id)
        freshness = max(last_event_ts, heartbeat_ts)
        if stale_after_ms > 0 and freshness > 0 and (now_ts - freshness) < stale_after_ms:
            skipped_active.append(mission_id)
            continue

        # 3. Genuinely orphaned → sweep to FAILED.
        #    Distinguish "had delivered work" (interrupted delivery) from a bare
        #    empty crash so the Outputs view can surface preserved artifacts.
        delivered = _delivered_artifacts(events)
        sweep_reason = "interrupted" if delivered else "crash_recovery"
        error_class = "MissionInterrupted" if delivered else "OrchestratorCrash"

        state_env = EventEnvelope(
            mission_id=mission_id,
            source_actor="system",
            ts_ms=now_ts,
            payload=MissionStateChanged(
                from_state=last_state,
                to_state=MissionState.FAILED.value,
                reason=sweep_reason,
            ),
        )
        await store.append_and_publish(state_env)

        fail_env = EventEnvelope(
            mission_id=mission_id,
            source_actor="system",
            ts_ms=now_ts,
            payload=MissionFailed(
                reason=sweep_reason,
                error_class=error_class,
                last_state=last_state,
                partial_artifacts=delivered,
            ),
        )
        await store.append_and_publish(fail_env)

        await store.upsert_mission(
            mission_id=mission_id,
            prompt=prompt,
            state=MissionState.FAILED.value,
            language="de",
            ts_ms=now_ts,
        )
        if delivered:
            interrupted_ids.append(mission_id)
        else:
            crash_ids.append(mission_id)
        recovered_ids.append(mission_id)

    if crash_ids:
        log.warning(
            "Mission recovery: marked %d mission(s) FAILED('crash_recovery'): %s",
            len(crash_ids),
            crash_ids,
        )
    if interrupted_ids:
        log.warning(
            "Mission recovery: marked %d mission(s) FAILED('interrupted') "
            "[partial work preserved]: %s",
            len(interrupted_ids),
            interrupted_ids,
        )
    if reconciled_ids:
        log.info(
            "Mission recovery: reconciled %d mission(s) to a recorded terminal "
            "event (no failure emitted): %s",
            len(reconciled_ids),
            reconciled_ids,
        )
    if skipped_active:
        log.info(
            "Mission recovery: skipped %d mission(s) as active (last event or "
            "heartbeat < %d ms ago — presumed owned by a live instance): %s",
            len(skipped_active),
            stale_after_ms,
            skipped_active,
        )
    return recovered_ids


def _last_terminal_state(events: list[EventEnvelope]) -> str | None:
    """Header state to reconcile to, from the LAST terminal event, or None.

    Events are ascending by seq, so the last matching entry wins — a mission
    that was wrongly crash_recovery'd and then truly approved (019e6fea:
    MissionFailed@seq3390 then MissionApproved@seq3396) reconciles to APPROVED.
    """
    result: str | None = None
    for env in events:
        state = _TERMINAL_EVENT_STATE.get(env.payload.event_type)
        if state is not None:
            result = state
    return result


def _delivered_artifacts(events: list[EventEnvelope]) -> list[str]:
    """artifact_uri(s) from WorkerDraftReady events that carried real work, or [].

    Non-empty result => the mission produced real work before going stale: the
    orphan was a DELIVERY interruption, not an empty crash. WorkerDraftReady
    schema (events.py:66): worker_id, artifact_uri, diff, tokens_used, cost_usd,
    session_id. "Real work" = a draft whose artifact_uri OR diff is non-empty.
    Per-worker sentinel + dedup so a multi-step mission with several diff-only
    workers does not emit duplicate entries.
    """
    seen: set[str] = set()
    artifacts: list[str] = []
    for env in events:
        p = env.payload
        if p.event_type != "WorkerDraftReady":
            continue
        uri = (getattr(p, "artifact_uri", "") or "").strip()
        diff = (getattr(p, "diff", "") or "").strip()
        entry: str | None = None
        if uri:
            entry = uri
        elif diff:
            worker_id = getattr(p, "worker_id", "") or env.mission_id
            entry = f"draft:{worker_id}"
        if entry and entry not in seen:
            seen.add(entry)
            artifacts.append(entry)
    return artifacts
