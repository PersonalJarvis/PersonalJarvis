"""Repo-root conftest for pytest discovery.

Pytest loads this file BEFORE all test modules and BEFORE `tests/conftest.py`.
We use this to add the repo root to `sys.path` so tests can import
top-level modules like `ui.orb.bus_bridge`.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Anti-hang guard: zombie gRPC threads blocking interpreter exit
# ---------------------------------------------------------------------------
# Root cause (measured 2026-06-11, scripts/diag_threads2.py): a test that makes
# a real Gemini / Vertex call leaves NON-DAEMON gRPC threads alive (``asyncio_0``,
# ``Thread-N (_connection_worker_thread)``). gRPC never marks them daemon and the
# google-genai client is never closed, so after the last test pytest blocks in
# ``threading._shutdown`` waiting for threads that never finish — the run "passes"
# in seconds but the process hangs for minutes until the shell timeout kills it
# (the 2-hour "test sweep" was minutes of tests + hours of hang-on-exit).
#
# Fix: at the very end of the session, if such zombies exist, flush and hard-exit
# with the REAL status (preserves pass/fail). A clean run (no leak) falls through
# to pytest's normal shutdown untouched. ``pytest_unconfigure`` is the last hook,
# so the terminal summary has already printed by the time this runs.
_FINAL_EXITSTATUS = {"code": 0}


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001, D401
    _FINAL_EXITSTATUS["code"] = int(exitstatus)


def pytest_unconfigure(config):  # noqa: ANN001, D401
    leaked = [
        t for t in threading.enumerate()
        if t is not threading.main_thread() and t.is_alive() and not t.daemon
    ]
    if not leaked:
        return
    import os

    names = ", ".join(sorted({t.name for t in leaked})[:6])
    sys.stderr.write(
        f"\n[conftest] hard-exit: {len(leaked)} non-daemon thread(s) would block "
        f"shutdown ({names}). See scripts/diag_threads2.py.\n"
    )
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_FINAL_EXITSTATUS["code"])
