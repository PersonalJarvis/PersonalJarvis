"""Startup-budget regression guard as a pytest entry point.

Wraps ``scripts/ci/check_boot_budget.py`` (one isolated cold boot through the
committed harness) so the TTU budget is enforceable from the test suite:

    pytest tests/integration/test_boot_budget.py -m slow

Marked slow + integration: it spawns a real cold boot (~10-40 s), so it never
runs in the fast unit sweep. Self-skips when the guard reports it could not
measure (exit 78, e.g. missing interpreter prerequisites) — a box that cannot
measure must not fake a pass OR a failure.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "scripts" / "ci" / "check_boot_budget.py"

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_cold_boot_stays_within_ttu_budget() -> None:
    proc = subprocess.run(  # noqa: S603 — our own guard, fixed argv
        [sys.executable, str(GUARD)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=480,
    )
    if proc.returncode == 78:
        pytest.skip("boot-budget guard could not measure on this host")
    assert proc.returncode == 0, (
        "startup budget exceeded — a change put work on the critical boot "
        f"path:\n{proc.stdout[-2000:]}\n{proc.stderr[-1000:]}"
    )
