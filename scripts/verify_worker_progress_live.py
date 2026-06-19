"""Live runtime verification for the mission live-progress feature (2026-06-15).

THE FEATURE: a long-but-healthy sub-agent mission must show what it is doing so
the user doesn't restart the app mid-run (and discard finished work as
app_shutdown). Two changes deliver it:
  A) ClaudeDirectWorker streams stdout line-by-line (codex already did).
  B) the orchestrator's worker drain loop emits throttled WorkerProgress events
     from the streamed activity — the producer that was missing, leaving the
     already-built WS -> store -> ReasoningPanel chain dormant.

THE PROOF this script provides: it drives the REAL mission pipeline
(`manager.dispatch` -> `kontrollierer.run_mission`) with the REAL live config
(whatever `[brain.sub_jarvis].provider` is set to) against an ISOLATED temp DB,
spawning a REAL worker subprocess, and asserts that WorkerProgress events with
human-readable notes were written to the store DURING the run. Zero
WorkerProgress = the feature is NOT live -> FAIL.

It NEVER touches the live data/missions.db and never restarts the running app,
so it is safe to run alongside a live desktop instance / a parallel session.

    "C:\\Program Files\\Python311\\python.exe" scripts/verify_worker_progress_live.py

Exit 0 only when >= 1 WorkerProgress event was emitted; exit 1 otherwise.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.missions.init import bootstrap_missions, shutdown_missions  # noqa: E402
from jarvis.missions.state_machine import MissionState  # noqa: E402

OK = "[OK]"
FAIL = "[FAIL]"
INFO = "[..]"

# A trivial, unambiguously-verifiable deliverable so a healthy worker produces
# real tool activity (a file write) fast — that activity is what the orchestrator
# must translate into WorkerProgress notes.
PROMPT = (
    "Create a new text file named `progress_probe.txt` in the current working "
    "directory whose entire contents are exactly the single line: PROGRESS-OK\n"
    "Do nothing else. Do not create any other files."
)

# Bound the wait so the script can't hang on a slow/quota'd worker — WorkerProgress
# is emitted EARLY (during the worker phase), so even a timeout still yields proof.
RUN_TIMEOUT_S = 200.0


async def verify() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="jarvis-progress-") as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "missions.db"
        isolation_root = tmp_path / "outputs"

        print(f"{INFO} Bootstrapping REAL mission stack (isolated DB, live config)…")
        result = await bootstrap_missions(
            db_path=db_path,
            isolation_root=isolation_root,
            repo_root=repo_root,
            recover_missions=False,  # never sweep; isolated DB anyway
        )
        manager = result["manager"]
        kontrollierer = result["kontrollierer"]

        try:
            from jarvis.core.config import load_config

            cfg = load_config()
            sub = getattr(cfg.brain, "sub_jarvis", None)
            print(f"{INFO} sub_jarvis.provider="
                  f"{getattr(sub, 'provider', None)!r} (the real worker backend).")
        except Exception as exc:  # noqa: BLE001
            print(f"{INFO} (config echo failed: {exc})")

        mission_id = await manager.dispatch(prompt=PROMPT, language="en")
        print(f"{INFO} Dispatched mission {mission_id[:13]}; running real worker…")

        final: MissionState | None = None
        try:
            final = await asyncio.wait_for(
                kontrollierer.run_mission(mission_id), timeout=RUN_TIMEOUT_S
            )
            print(f"{INFO} Mission final state: {final.value}")
        except asyncio.TimeoutError:
            print(f"{INFO} run_mission exceeded {RUN_TIMEOUT_S:.0f}s — reading the "
                  f"events emitted so far (WorkerProgress fires early).")

        events = await manager.store.events_for_mission(mission_id)
        by_type: dict[str, int] = {}
        worker_backend = None
        progress: list[tuple[str, str]] = []  # (worker_id, note)
        for env in events:
            p = env.payload
            et = getattr(p, "event_type", "")
            by_type[et] = by_type.get(et, 0) + 1
            if et == "WorkerSpawned":
                worker_backend = getattr(p, "cli", None)
            if et == "WorkerProgress":
                progress.append((getattr(p, "worker_id", ""), getattr(p, "note", "") or ""))

        await shutdown_missions(result)

        print(f"\n{INFO} Worker backend (cli): {worker_backend!r}")
        print(f"{INFO} Event-type counts: {by_type}")
        print(f"{INFO} WorkerProgress events emitted: {len(progress)}")
        for i, (wid, note) in enumerate(progress[:12], 1):
            print(f"    {i:>2}. [{wid}] {note}")
        if len(progress) > 12:
            print(f"    … (+{len(progress) - 12} more)")

        if progress:
            print(f"\n{OK} LIVE-PROGRESS GREEN — the orchestrator emitted "
                  f"{len(progress)} WorkerProgress event(s) with notes during a "
                  f"real {worker_backend!r} mission. The producer is live.")
            return 0
        print(f"\n{FAIL} No WorkerProgress events were emitted — the feature is "
              f"NOT live in this interpreter.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(verify()))
