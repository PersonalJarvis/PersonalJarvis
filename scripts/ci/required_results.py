#!/usr/bin/env python3
"""The single aggregate CI gate ("CI gate" — the only required check).

Input: ``toJSON(needs)`` of the gate job on stdin. Branch protection and the
merge train require ONLY this check, so adding, renaming or path-gating a job
never needs a settings change.

* Any ``failure`` / ``cancelled`` result fails the gate.
* ``skipped`` passes in normal mode — a lane the change cannot affect.
* ``--strict`` (nightly, release) fails a skipped job too, except the jobs in
  :data:`STRICT_SKIP_OK` that can never run on those events.

    echo "$NEEDS" | python scripts/ci/required_results.py [--strict]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# Jobs that only exist on some events: pull-request-only checks and manual
# soak runs. A strict run may skip these and nothing else.
STRICT_SKIP_OK = frozenset({"pr-policy", "soak"})


def evaluate(needs: dict[str, dict[str, Any]] | None, *, strict: bool = False) -> dict[str, Any]:
    failed: list[str] = []
    skipped: list[str] = []
    entries = needs or {}
    if not entries:
        failed.append("<no-needs>")
    for name, info in entries.items():
        result = (info or {}).get("result")
        if result == "success":
            continue
        if result == "skipped" and (not strict or name in STRICT_SKIP_OK):
            skipped.append(name)
            continue
        failed.append(name)
    return {"ok": not failed, "failed": sorted(failed), "skipped": sorted(skipped)}


def render(needs: dict[str, dict[str, Any]] | None, verdict: dict[str, Any]) -> list[str]:
    lines = ["| job | result |", "|---|---|"]
    for name in sorted(needs or {}):
        result = (needs[name] or {}).get("result", "unknown")  # type: ignore[index]
        icon = {"success": "pass", "skipped": "skip"}.get(result, "FAIL")
        lines.append(f"| {name} | {icon} ({result}) |")
    if verdict["failed"]:
        lines.append("")
        lines.append(f"**Failed:** {', '.join(verdict['failed'])}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="A skipped job fails too.")
    args = parser.parse_args(argv)

    needs = json.load(sys.stdin)
    verdict = evaluate(needs, strict=args.strict)
    report = render(needs, verdict)
    print("\n".join(report))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## CI gate\n\n" + "\n".join(report) + "\n")
    if not verdict["ok"]:
        print(f"::error::CI gate failed: {', '.join(verdict['failed'])}")
        return 1
    print("CI gate: all required jobs passed" + (" (strict)" if args.strict else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
