"""Smoke test Phase 1: Foundation (event schema + bus + store + manager + recovery).

Runs end-to-end without pytest, checks the acceptance criteria from
docs/phase6-prompt-chain.md for Phase 1. Exit 0 on success, exit 1 on failure.

What is verified:
1. Clean start without pre-existing missions returns recovery=[].
2. Happy path PENDING -> RUNNING -> CRITIQUING -> APPROVED emits 4 events.
3. seq is monotonic 1..N, with no gaps.
4. Crash simulation (manager.stop() without a terminal state) + restart is
   ACTIVITY-AWARE (fix 9f5e043b): a mission whose last event is recent is
   presumed owned by a live orchestrator and is SKIPPED (not swept), while a
   genuinely stale orphan (no activity past the window) is swept to FAILED with
   MissionStateChanged + MissionFailed on the new bus.
5. Terminal missions (APPROVED) are NOT recovered.
6. events_since(0) returns all persisted events with no gaps.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

# Repo root in sys.path so `from jarvis.missions...` works
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.missions.events import EventEnvelope, now_ms  # noqa: E402
from jarvis.missions.manager import MissionManager  # noqa: E402
from jarvis.missions.recovery import (  # noqa: E402
    RECOVERY_STALE_AFTER_MS,
    startup_recover,
)
from jarvis.missions.state_machine import MissionState  # noqa: E402

OK = "[OK]"
FAIL = "[FAIL]"


async def smoke() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "smoke_missions.db"

        # --- Section 1: clean start + happy-path ---

        m1 = MissionManager(db_path)
        recovered = await m1.start()
        if recovered != []:
            failures.append(f"start without pre-state returns recovery={recovered}")
        else:
            print(f"{OK} clean start, no recovery")

        mid1 = await m1.dispatch(prompt="smoke test", language="de")
        await m1.transition_state(mid1, MissionState.RUNNING, reason="worker-spawn")
        await m1.transition_state(mid1, MissionState.CRITIQUING, reason="diff-ready")
        await m1.transition_state(
            mid1, MissionState.APPROVED, reason="critic-approved"
        )

        events1 = await m1.store.events_for_mission(mid1)
        if len(events1) != 4:
            failures.append(f"happy path returns {len(events1)} events, expected 4")
        else:
            print(f"{OK} happy-path emits 4 events")

        seqs1 = [e.seq for e in events1]
        if seqs1 != [1, 2, 3, 4]:
            failures.append(f"seq not [1,2,3,4]: {seqs1}")
        else:
            print(f"{OK} seq monoton [1,2,3,4]")

        types1 = [e.payload.event_type for e in events1]
        expected_types = [
            "MissionDispatched",
            "MissionStateChanged",
            "MissionStateChanged",
            "MissionStateChanged",
        ]
        if types1 != expected_types:
            failures.append(f"event-types {types1} != {expected_types}")
        else:
            print(f"{OK} event types consistent")

        # --- Section 2: crash-simulation + ACTIVITY-AWARE recovery ---
        # (fix 9f5e043b: a fresh crashed mission is presumed owned by a live
        # orchestrator and must NOT be swept; only a genuinely stale orphan is.)

        mid2 = await m1.dispatch(prompt="will-crash")
        await m1.transition_state(mid2, MissionState.RUNNING, reason="worker-spawn")
        await m1.stop()
        print(f"{OK} crash simulation: stop without a terminal state")

        m2 = MissionManager(db_path)
        bus_received: list[EventEnvelope] = []

        async def collect(e: EventEnvelope) -> None:
            bus_received.append(e)

        m2.bus.subscribe_all(collect)

        # 2a: default 30-min guard — mid2's last event is FRESH, so the live-work
        # guard must protect it (no sweep, state stays RUNNING). This is the very
        # behaviour that fixed the crash_recovery false-negative.
        recovered_fresh = await m2.start()
        if mid2 in recovered_fresh:
            failures.append(
                f"FRESH crashed mission incorrectly swept: {recovered_fresh}"
            )
        else:
            print(f"{OK} activity-guard: fresh crashed mission NOT swept")

        view_fresh = await m2.mission(mid2)
        if view_fresh is None or view_fresh.state != MissionState.RUNNING:
            failures.append(
                f"protected mission state "
                f"{view_fresh.state if view_fresh else None} != RUNNING"
            )
        else:
            print(f"{OK} protected mission stays RUNNING")

        # 2b: a GENUINELY stale orphan (its last event is older than the window)
        # IS a real crash and gets swept to FAILED. We model "past the window" by
        # asking the recovery to evaluate at a point > RECOVERY_STALE_AFTER_MS in
        # the future — exactly how test_recovery_staleness.py proves the sweep.
        future_now = now_ms() + RECOVERY_STALE_AFTER_MS + 60_000
        recovered_stale = await startup_recover(m2.store, now=future_now)

        if mid2 not in recovered_stale:
            failures.append(
                f"stale orphan NOT swept: {recovered_stale}"
            )
        else:
            print(f"{OK} stale orphan -> recovered")

        view_stale = await m2.mission(mid2)
        if view_stale is None or view_stale.state != MissionState.FAILED:
            failures.append(
                f"stale orphan state "
                f"{view_stale.state if view_stale else None} != FAILED"
            )
        else:
            print(f"{OK} stale orphan is FAILED after recovery")

        recovery_types = [e.payload.event_type for e in bus_received]
        if (
            "MissionFailed" not in recovery_types
            or "MissionStateChanged" not in recovery_types
        ):
            failures.append(
                f"recovery emits {recovery_types}, missing MissionFailed/StateChange"
            )
        else:
            print(f"{OK} recovery emits MissionFailed + MissionStateChanged on the bus")

        # mid1 is APPROVED — must NOT be recovered in EITHER of the two sweeps
        if mid1 in recovered_fresh or mid1 in recovered_stale:
            failures.append(
                "recovered incorrectly contains mid1 (APPROVED)"
            )
        else:
            print(f"{OK} terminal missions not recovered")

        # --- Section 3: events_since(0) has no gaps ---

        all_events = await m2.store.events_since(0)
        # mid1: 4 (dispatch + 3 state-changes), terminal -> never swept.
        # mid2: 2 (dispatch + 1 state-change) + 2 stale-recovery (state-change +
        #       failed). The fresh-guard pass in 2a emits nothing. = 8 total.
        if len(all_events) != 8:
            failures.append(f"events_since(0) returns {len(all_events)}, expected 8")
        else:
            print(f"{OK} events_since(0) returns all 8 events")

        all_seqs = [e.seq for e in all_events]
        if all_seqs != list(range(1, 9)):
            failures.append(f"global seq not 1..8: {all_seqs}")
        else:
            print(f"{OK} global seq monotonic 1..8")

        await m2.stop()

    print()
    if failures:
        print(f"{FAIL} {len(failures)} smoke-failures:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"{OK} ALL SMOKE CHECKS GREEN -- Phase 1 Foundation ready.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke()))
