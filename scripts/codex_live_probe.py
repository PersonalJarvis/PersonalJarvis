"""Drive real headless ChatGPT-Live calls against the codex-subscription path.

The reproduction tool this provider never had: every earlier fix round was
verified by reading ``data/jarvis_desktop.log`` after a live call died. This
CLI runs scripted calls (committed speech fixtures, real adapter, real
app-server, real WebRTC) and prints a PASS/WARN/FAIL verdict per scenario.

Every call consumes the ChatGPT subscription's realtime usage - the run is
capped (``--max-calls``, default 5; more needs ``--yes-really``). The desktop
app must be STOPPED first: the fail-closed voice-profile lock otherwise
refuses the probe (exit 3).

Usage::

    python scripts/codex_live_probe.py --dump                # contract discovery
    python scripts/codex_live_probe.py --scenario three_turns_de
    python scripts/codex_live_probe.py --scenario three_turns_de \
        --scenario barge_in_followup --scenario silence_no_selftalk
    python scripts/diag_voice_sessions.py --harness <out>/round.jsonl

Exit codes: 0 pass · 1 assert failed · 2 environment missing · 3 the app
holds the profile lock.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

# The worktree the script lives in is the code that runs, regardless of where
# the editable install points (BUG-006 class) - same pattern as
# scripts/diag_voice_sessions.py.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from jarvis.diagnostics.realtime_probe import (  # noqa: E402
    ProbeEnvironmentError,
    load_scenario,
    run_dump,
    run_scenario,
)

DEFAULT_MAX_CALLS = 5


def _print_summary(summary: dict) -> None:
    verdict = "PASS" if summary.get("passed") else "FAIL"
    print(f"\nscenario {summary.get('scenario')}: {verdict}")
    for name, entry in (summary.get("asserts") or {}).items():
        print(f"  [{entry['status']:4}] {name}: {entry['detail']}")
    postmortem = summary.get("postmortem") or {}
    if postmortem:
        print(
            f"  postmortem: turns={postmortem.get('turns_completed', 0)} "
            f"ready={postmortem.get('ready_ms', 0)}ms "
            f"first_audio={postmortem.get('first_audio_ms', 0)}ms "
            f"rebuilds={postmortem.get('rebuilds', 0)}"
        )


async def _run(args: argparse.Namespace) -> int:
    calls_planned = (1 if args.dump else 0) + len(args.scenario)
    if calls_planned == 0:
        print("nothing to do: pass --dump and/or --scenario <name>")
        return 2
    if calls_planned > args.max_calls and not args.yes_really:
        print(
            f"{calls_planned} live calls exceed the cap of {args.max_calls}; "
            "re-run with --yes-really if you mean it"
        )
        return 2
    print(
        f"NOTE: {calls_planned} live call(s) will bill the ChatGPT "
        "subscription's realtime usage."
    )

    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = args.out or (REPO_ROOT / "data" / "diagnostics" / "codex_live" / stamp)
    base = Path(base)
    exit_code = 0

    if args.dump:
        print("\n-- dump round (contract discovery) --")
        summary = await run_dump(out_dir=base / "dump")
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        terminal = summary.get("terminal_in_frozenset") or []
        candidates = summary.get("terminal_candidates") or []
        if terminal:
            print(f"terminal response item observed: {terminal} - IN _TERMINAL_RESPONSE_ITEMS")
        elif candidates:
            print(
                f"terminal response item observed: {candidates} - "
                "NOT IN _TERMINAL_RESPONSE_ITEMS (update the frozenset)"
            )
        else:
            print(
                "no terminal-shaped item observed - the quiescence backstop "
                "remains the only boundary"
            )

    budgets = {
        "ready_s": args.budget_ready_s,
        "first_audio_s": args.budget_first_audio_s,
        "enforce": args.enforce_budgets,
    }
    for name in args.scenario:
        print(f"\n-- scenario {name} --")
        scenario = load_scenario(name)
        summary = await run_scenario(
            scenario,
            out_dir=base / name,
            budgets=budgets,
            with_delegate=args.with_delegate,
        )
        _print_summary(summary)
        if not summary.get("passed"):
            exit_code = 1

    print(f"\ncaptures under {base}")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--dump", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    parser.add_argument("--yes-really", action="store_true")
    parser.add_argument("--budget-ready-s", type=float, default=4.0)
    parser.add_argument("--budget-first-audio-s", type=float, default=6.0)
    parser.add_argument(
        "--enforce-budgets",
        action="store_true",
        help="spawn budget overruns FAIL instead of WARN (the B6 flip)",
    )
    parser.add_argument(
        "--with-delegate",
        action="store_true",
        help="wire the configured brain so the delegation scenario can run",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except ProbeEnvironmentError as exc:
        print(f"probe environment: {exc}")
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
