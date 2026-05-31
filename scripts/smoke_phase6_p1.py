"""Smoke-Test Phase 1: Foundation (Event-Schema + Bus + Store + Manager + Recovery).

Laeuft End-to-End ohne pytest, prueft die Acceptance-Kriterien aus
docs/phase6-prompt-chain.md fuer Phase 1. Exit 0 bei Erfolg, Exit 1 bei Fehler.

Was wird verifiziert:
1. Clean start ohne pre-existing Missions liefert recovery=[].
2. Happy-Path PENDING -> RUNNING -> CRITIQUING -> APPROVED emittiert 4 Events.
3. seq ist monoton 1..N, lueckenlos.
4. Crash-Simulation (manager.stop() ohne Endzustand) + Restart fuehrt zur
   Recovery: stale Mission wird FAILED, MissionStateChanged + MissionFailed
   landen auf dem neuen Bus.
5. Terminale Missions (APPROVED) werden NICHT recovered.
6. events_since(0) liefert lueckenlos alle persistierten Events.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

# Repo-Root in sys.path damit `from jarvis.missions...` funktioniert
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.missions.events import EventEnvelope  # noqa: E402
from jarvis.missions.manager import MissionManager  # noqa: E402
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
            failures.append(f"start ohne pre-state liefert recovery={recovered}")
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
            failures.append(f"happy-path liefert {len(events1)} events, erwartet 4")
        else:
            print(f"{OK} happy-path emits 4 events")

        seqs1 = [e.seq for e in events1]
        if seqs1 != [1, 2, 3, 4]:
            failures.append(f"seq nicht [1,2,3,4]: {seqs1}")
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
            print(f"{OK} event-types konsistent")

        # --- Section 2: crash-simulation + recovery ---

        mid2 = await m1.dispatch(prompt="will-crash")
        await m1.transition_state(mid2, MissionState.RUNNING, reason="worker-spawn")
        await m1.stop()
        print(f"{OK} crash-simulation: stop ohne endzustand")

        m2 = MissionManager(db_path)
        bus_received: list[EventEnvelope] = []

        async def collect(e: EventEnvelope) -> None:
            bus_received.append(e)

        m2.bus.subscribe_all(collect)
        recovered2 = await m2.start()

        if mid2 not in recovered2:
            failures.append(f"recovery enthaelt {mid2!r} nicht: {recovered2}")
        else:
            print(f"{OK} recovery markiert stale RUNNING")

        view2 = await m2.mission(mid2)
        if view2 is None or view2.state != MissionState.FAILED:
            failures.append(
                f"mid2 state {view2.state if view2 else None} != FAILED"
            )
        else:
            print(f"{OK} mid2 ist FAILED nach recovery")

        recovery_types = [e.payload.event_type for e in bus_received]
        if (
            "MissionFailed" not in recovery_types
            or "MissionStateChanged" not in recovery_types
        ):
            failures.append(
                f"recovery emits {recovery_types}, missing MissionFailed/StateChange"
            )
        else:
            print(f"{OK} recovery emits MissionFailed + MissionStateChanged auf bus")

        # mid1 ist APPROVED — darf NICHT recovered werden
        if mid1 in recovered2:
            failures.append(
                f"recovered enthaelt faelschlich mid1 (APPROVED): {recovered2}"
            )
        else:
            print(f"{OK} terminal missions nicht recovered")

        # --- Section 3: events_since(0) lueckenlos ---

        all_events = await m2.store.events_since(0)
        # mid1: 4 (dispatch + 3 state-changes)
        # mid2: 2 (dispatch + 1 state-change) + 2 recovery (state-change + failed)
        # = 8 total
        if len(all_events) != 8:
            failures.append(f"events_since(0) liefert {len(all_events)}, erwartet 8")
        else:
            print(f"{OK} events_since(0) liefert alle 8 events")

        all_seqs = [e.seq for e in all_events]
        if all_seqs != list(range(1, 9)):
            failures.append(f"global seq nicht 1..8: {all_seqs}")
        else:
            print(f"{OK} global seq monoton 1..8")

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
