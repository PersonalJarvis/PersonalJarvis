"""Replay scripted spoken turns and print what Jarvis would have said.

The point: find out whether a turn behaves WITHOUT phoning Jarvis. Each scenario
in ``tests/fixtures/voice_scenarios/scenarios.yaml`` names what the user said and
which tools succeed, fail, or get refused; this drives the real
``RealtimeVoiceSession`` over a scripted wire and shows the spoken reply.

No model runs, so a full pass costs nothing and takes a couple of seconds. What
it proves is what the SESSION decides — that a cause is named, that a gated call
is not mistaken for a breakdown, that a half-done turn reports its work and is
told to finish the rest. Whether a live model obeys the prompt directive is a
different question and needs a real call.

Usage:
    python scripts/voice_scenario_run.py                 # all scenarios
    python scripts/voice_scenario_run.py four-orders     # substring filter
    python scripts/voice_scenario_run.py --verbose       # + prompts and tools

Exit code is 1 when any scenario misbehaves, so this doubles as a check in a
script. The same file is asserted by tests/unit/realtime/test_voice_scenarios.py.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — a console that cannot be reconfigured still runs
        print(f"(console encoding left as-is: {exc})", file=sys.stderr)

from tests.fakes.voice_scenario import (  # noqa: E402 — needs the sys.path above
    Scenario,
    ScenarioResult,
    load_scenarios,
    run_scenario,
)

SCENARIO_FILE = REPO / "tests" / "fixtures" / "voice_scenarios" / "scenarios.yaml"


def _report(result: ScenarioResult, *, verbose: bool) -> bool:
    """Print one scenario's outcome; return True when it behaved."""
    problems = result.failures()
    mark = "FAIL" if problems else " ok "
    print(f"[{mark}] {result.scenario.name}")
    print(f"       user     : {result.scenario.user}")
    print(f"       spoken   : {result.spoken_text or '(nothing)'}")
    print(f"       continues: {'yes' if result.asked_to_finish else 'no'}")
    if verbose:
        print(f"       tools    : {', '.join(result.tool_calls) or '(none)'}")
        for prompt in result.prompts:
            print(f"       prompt   : {prompt[:160]}")
    for problem in problems:
        print(f"       -> {problem}")
    return not problems


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "filter",
        nargs="?",
        default="",
        help="only run scenarios whose name contains this substring",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="also print the tools that ran and the prompts the session injected",
    )
    parser.add_argument(
        "--file",
        default=str(SCENARIO_FILE),
        help="scenario file to run (default: the checked-in set)",
    )
    args = parser.parse_args(argv)

    # Session logging is diagnostic noise here; the report IS the output.
    logging.disable(logging.CRITICAL)

    scenarios: list[Scenario] = load_scenarios(args.file)
    if args.filter:
        scenarios = [s for s in scenarios if args.filter in s.name]
    if not scenarios:
        print(f"No scenario matches {args.filter!r} in {args.file}")
        return 1

    good = 0
    for scenario in scenarios:
        result = await run_scenario(scenario)
        good += _report(result, verbose=args.verbose)
    bad = len(scenarios) - good
    print()
    print(f"{good}/{len(scenarios)} behaved" + (f", {bad} did NOT" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
