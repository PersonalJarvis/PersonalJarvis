"""Final-Validation Agent 3: live probe of the EPERM-defuse seed.

Calls ``build_worker_env(run_dir=<tmp>)`` and asserts:
- ``<run_dir>/openclaw_state/plugin-skills/browser-automation/`` exists
- a ``SKILL.md`` marker file lives inside and has non-zero size
- a second call leaves the marker mtime untouched (idempotent)

Run:
    python scripts/_final_validate_3_critic.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Make project import path work when invoked from anywhere.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.missions.isolation.env import build_worker_env  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="final-validate-3-") as td:
        run_dir = Path(td) / "missions" / "probe"
        env = build_worker_env(run_dir=run_dir)
        state_dir = Path(env["OPENCLAW_STATE_DIR"])
        target = state_dir / "plugin-skills" / "browser-automation"
        marker = target / "SKILL.md"

        print(f"[probe] OPENCLAW_STATE_DIR = {state_dir}")
        print(f"[probe] browser-automation dir exists = {target.is_dir()}")
        print(f"[probe] SKILL.md exists = {marker.is_file()}")
        if marker.is_file():
            print(f"[probe] SKILL.md size = {marker.stat().st_size} bytes")
            first_mtime = marker.stat().st_mtime_ns
        else:
            print("[probe] FAIL: SKILL.md missing")
            return 2

        # Second call must not blow up nor re-write the marker.
        build_worker_env(run_dir=run_dir)
        second_mtime = marker.stat().st_mtime_ns

        print(f"[probe] idempotent (mtime equal) = {first_mtime == second_mtime}")

        ok = (
            target.is_dir()
            and marker.is_file()
            and marker.stat().st_size > 0
            and first_mtime == second_mtime
        )
        print(f"[probe] VERDICT = {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
