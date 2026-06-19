"""Final live-verdict run for the BUG-LIVE-01..05 + BUG-ALT-03 fix batch.

Boots the full Phase-6 mission stack (Manager + Kontrollierer + Worker +
Critic), dispatches a single mission, drives the loop until it reaches
APPROVED or FAILED, and prints the verdict together with the mission's
event timeline. This is the "voice-test prompt of your choice" run: the
prompt is single-line and apostrophe-clean so BUG-ALT-03's old failure
mode can't mask anything, and the file path is something the worker can
trivially produce.

Even when external APIs are quota-locked today, the mission's state
transitions (PENDING -> ... -> APPROVED|FAILED) and the event payloads
prove that the integrated fixes route correctly through the loop.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from jarvis.missions.init import bootstrap_missions
from jarvis.missions.state_machine import MissionState

PROMPT = (
    "Write a one-line file named verdict.txt with the single word OK. "
    "Then exit."
)


async def main() -> int:
    runtime = PROJECT / "data" / "_final_verdict_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    db_path = runtime / "missions.db"
    isolation_root = runtime / "isolation"
    if db_path.exists():
        db_path.unlink()
    isolation_root.mkdir(parents=True, exist_ok=True)

    bundle = await bootstrap_missions(
        db_path=db_path,
        isolation_root=isolation_root,
        repo_root=PROJECT,
        tts_speak_fn=None,        # headless: no voice
        brain_caller=None,        # heuristic 1-step decompose
    )

    manager = bundle["manager"]
    kontrollierer = bundle["kontrollierer"]

    print("=" * 60)
    print("Final live verdict — full Phase-6 stack with all 6 fixes applied")
    print("=" * 60)
    print(f"prompt: {PROMPT!r}")
    started = time.time()

    mission_id = await manager.dispatch(prompt=PROMPT)
    print(f"\nmission_id: {mission_id}")

    end_state = await kontrollierer.run_mission(mission_id)
    duration = time.time() - started
    view = await manager.mission(mission_id)

    print(f"\nfinal mission state: {end_state}")
    print(f"wall time: {duration:.1f}s")
    print(f"view.state: {view.state if view else 'None'}")

    if end_state == MissionState.APPROVED:
        verdict_label = "Mission_Approved"
    elif end_state == MissionState.FAILED:
        verdict_label = "Mission_Failed"
    elif end_state == MissionState.TIMED_OUT:
        verdict_label = "Mission_Failed (timeout)"
    elif end_state == MissionState.CANCELLED:
        verdict_label = "Mission_Failed (cancelled)"
    else:
        verdict_label = f"Mission_Other ({end_state})"

    print(f"\nVERDICT: {verdict_label}")

    # Dump the last ~12 events for forensics.
    events = manager.store.list_for_mission(mission_id)
    print(f"\nevent count: {len(events)}")
    for ev in events[-12:]:
        payload_type = type(ev.payload).__name__
        try:
            payload_summary = json.dumps(
                ev.payload.__dict__, default=str
            )[:140]
        except Exception:
            payload_summary = repr(ev.payload)[:140]
        print(f"  seq={ev.seq:>3}  {payload_type:<30s}  {payload_summary}")

    await manager.stop()
    return 0 if end_state == MissionState.APPROVED else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
