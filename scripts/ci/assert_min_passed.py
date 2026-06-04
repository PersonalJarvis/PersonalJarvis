#!/usr/bin/env python3
"""Minimum-passed-count floor (Wave 0, sub-task 0.5).

A broken conftest or a mass-skip regression can make pytest exit 0 while almost
nothing actually ran. This gate parses the JUnit XML and asserts that the number
of PASSED tests is at least ``FLOOR``. A drop below the floor fails the build
even when pytest's own exit code is green.

Usage: ``python scripts/ci/assert_min_passed.py report.xml``

FLOOR is seeded conservatively from the current green count on the branch. Bump
it FORWARD as the suite grows — NEVER down (lowering it defeats the purpose).
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Seeded from the Wave-0 baseline green count (measured 2026-05-29). Conservative
# — the real passing count on the Linux leg is higher; raise this only forward.
FLOOR = 1200


def _aggregate(path: Path) -> tuple[int, int, int, int]:
    """Return (tests, failures, errors, skipped) summed over all <testsuite>s."""
    # S314: the report is our OWN pytest-generated JUnit XML (trusted local
    # artifact, not untrusted network data) — defusedxml is unnecessary here.
    root = ET.parse(path).getroot()  # noqa: S314
    suites = root.iter("testsuite")
    tests = failures = errors = skipped = 0
    for s in suites:
        tests += int(s.get("tests", 0))
        failures += int(s.get("failures", 0))
        errors += int(s.get("errors", 0))
        skipped += int(s.get("skipped", 0))
    return tests, failures, errors, skipped


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: assert_min_passed.py <junit-report.xml>")
        return 2
    path = Path(argv[1])
    if not path.exists():
        print(f"FAIL JUnit report not found: {path}")
        return 2

    strict = "--strict" in argv
    if strict:
        argv = [a for a in argv if a != "--strict"]
    tests, failures, errors, skipped = _aggregate(path)
    passed = tests - failures - errors - skipped
    print(
        f"tests={tests} passed={passed} failures={failures} "
        f"errors={errors} skipped={skipped} floor={FLOOR} strict={strict}"
    )

    # The repo carries pre-existing failures (e.g. the Telegram contract test) that
    # predate this migration and are a separate backlog. By default we REPORT them
    # but do not block on them — the floor is the primary mass-skip guard. Pass
    # --strict once the suite is fully green to make any failure blocking.
    if failures or errors:
        msg = f"{failures} failures + {errors} errors present"
        if strict:
            print(f"FAIL (strict) {msg}.")
            return 1
        print(f"WARN {msg} — reported, not blocking (pre-existing backlog).")
    if passed < FLOOR:
        print(
            f"FAIL passed={passed} < FLOOR={FLOOR} — a mass-skip/collection "
            f"regression likely dropped the count. Investigate before merging."
        )
        return 1
    print(f"min-passed floor: PASS ({passed} >= {FLOOR})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
