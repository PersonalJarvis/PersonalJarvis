"""End-to-end verification for the sub-mission provider-fallback fix (2026-06-08).

THE BUG (forensic `data/missions.db`, missions 019ea82e* / 019ea830*): with
``[brain.sub_jarvis].provider`` set to anything but ``claude-api`` / codex
(here: ``grok``), every mission died in ~3 s because ``ClaudeDirectWorker``
refused to run ("primary provider is grok, expected claude-api") — a guard that
assumed a fall-through to the (now-removed) OpenClaw SubJarvisWorker.

THE PROOF this script provides: it drives the REAL mission pipeline
(``manager.dispatch`` -> ``kontrollierer.run_mission``) with the REAL live config
(grok sub-agent) against an ISOLATED temp DB (never the live one), spawning real
``claude`` Max-OAuth workers, and asserts the mission reaches APPROVED — N times.
A single ``primary provider is grok`` refusal = hard FAIL.

This is the KPI for "the sub-missions stop failing forever". Run:

    "C:\\Program Files\\Python311\\python.exe" scripts/verify_submission_provider_fix.py [--rounds 3]

Exit 0 only when >= ``--rounds`` rounds reached APPROVED with zero
provider-refusal failures; exit 1 otherwise.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.missions.init import bootstrap_missions  # noqa: E402
from jarvis.missions.state_machine import MissionState  # noqa: E402

OK = "[OK]"
FAIL = "[FAIL]"
INFO = "[..]"

# A trivial, unambiguously-verifiable deliverable so a healthy worker+critic
# always APPROVES — any failure is then a pipeline defect, not task difficulty.
PROMPT_TEMPLATE = (
    "Create a new text file named `verify_ok_{n}.txt` in the current working "
    "directory whose entire contents are exactly the single line: ROUND-{n}-OK\n"
    "Do nothing else. Do not create any other files."
)

# Reason strings that mean THE BUG we are hunting is still present.
_BUG_REASONS = {"task_error"}
_PROVIDER_REFUSAL_MARKER = "expected claude-api"


async def _failure_detail(store, mission_id: str) -> tuple[str | None, str | None]:
    """Return (reason, worker_error_text) for a failed mission, if any."""
    reason = None
    worker_err = None
    events = await store.events_for_mission(mission_id)
    for env in events:
        p = env.payload
        et = getattr(p, "event_type", "")
        if et == "MissionFailed":
            reason = getattr(p, "reason", None)
        if et == "ClaudeResult" or et == "WorkerKilled":
            txt = getattr(p, "result", None) or getattr(p, "reason", None)
            if txt and _PROVIDER_REFUSAL_MARKER in str(txt):
                worker_err = str(txt)
    return reason, worker_err


async def _run_round(kontrollierer, manager, n: int) -> tuple[bool, str]:
    """Dispatch one real mission. Returns (approved, detail)."""
    prompt = PROMPT_TEMPLATE.format(n=n)
    mission_id = await manager.dispatch(prompt=prompt, language="en")
    final: MissionState = await kontrollierer.run_mission(mission_id)

    if final == MissionState.APPROVED:
        return True, f"APPROVED (mission {mission_id[:8]})"

    reason, worker_err = await _failure_detail(manager.store, mission_id)
    if worker_err and _PROVIDER_REFUSAL_MARKER in worker_err:
        return False, f"PROVIDER-REFUSAL BUG STILL PRESENT: {worker_err!r}"
    return False, f"state={final.value} reason={reason!r} (mission {mission_id[:8]})"


async def verify(rounds_target: int, max_attempts: int) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="jarvis-verify-") as tmp:
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

        # Echo the exact bug condition we are exercising.
        try:
            from jarvis.core.config import load_config

            cfg = load_config()
            sub = getattr(cfg.brain, "sub_jarvis", None)
            sub_provider = getattr(sub, "provider", None) if sub else None
            print(
                f"{INFO} Live config: brain.primary={cfg.brain.primary!r}, "
                f"sub_jarvis.provider={sub_provider!r} "
                f"(the value that triggered the bug).",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{INFO} (config echo failed: {exc})")

        approved = 0
        attempts = 0
        try:
            while approved < rounds_target and attempts < max_attempts:
                attempts += 1
                print(f"\n{INFO} Round {attempts}: dispatching real claude worker…")
                ok, detail = await _run_round(kontrollierer, manager, attempts)
                if ok:
                    approved += 1
                    print(f"{OK} Round {attempts}: {detail}  [{approved}/{rounds_target} approved]")
                else:
                    print(f"{FAIL} Round {attempts}: {detail}")
                    if "PROVIDER-REFUSAL BUG" in detail:
                        print(f"{FAIL} The fix is NOT live in this interpreter — aborting.")
                        return 1
        finally:
            from jarvis.missions.init import shutdown_missions

            await shutdown_missions(result)

        print()
        if approved >= rounds_target:
            print(f"{OK} VERIFICATION GREEN — {approved} successful rounds "
                  f"(>= {rounds_target}), zero provider-refusal failures.")
            return 0
        print(f"{FAIL} only {approved}/{rounds_target} rounds approved in "
              f"{attempts} attempts.")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3,
                    help="number of APPROVED rounds required (default 3)")
    ap.add_argument("--max-attempts", type=int, default=6,
                    help="hard cap on dispatch attempts (default 6)")
    args = ap.parse_args()
    return asyncio.run(verify(args.rounds, args.max_attempts))


if __name__ == "__main__":
    sys.exit(main())
