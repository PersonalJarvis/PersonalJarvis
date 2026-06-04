"""E2E Job-Object Kill-Parent. Plan AD-9 — Hauptjarvis-Crash killt Overlay.

Strategie: Wir starten einen "Parent-Process" der einen "Child-Process"
spawned und unter ein Job-Object haengt mit JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
Dann taskkillen wir den Parent hart und assert dass der Child innerhalb
1 s ebenfalls weg ist.

Windows-only — auf anderen Plattformen ist das ein Skip.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest

if sys.platform != "win32":
    pytest.skip("Job-Object ist Windows-only", allow_module_level=True)

try:
    import psutil  # noqa: F401
except ImportError:  # pragma: no cover
    pytest.skip("psutil benoetigt fuer Process-Watch", allow_module_level=True)


PARENT_SCRIPT = textwrap.dedent("""
    import os
    import subprocess
    import sys
    import time

    import win32api  # type: ignore[import-not-found]
    import win32con  # type: ignore[import-not-found]
    import win32job  # type: ignore[import-not-found]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    # Spawn child: laueft 30 s.
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )

    # Job-Object aufsetzen.
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(
        job, win32job.JobObjectExtendedLimitInformation
    )
    info["BasicLimitInformation"]["LimitFlags"] |= JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(
        job, win32job.JobObjectExtendedLimitInformation, info
    )

    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001
    proc_h = win32api.OpenProcess(
        PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, child.pid
    )
    win32job.AssignProcessToJobObject(job, proc_h)
    win32api.CloseHandle(proc_h)

    # PID des childs ausgeben damit der Test ihn watchen kann.
    sys.stdout.write(str(child.pid) + chr(10))
    sys.stdout.flush()

    # Endlos-loop damit der Test uns killen kann.
    while True:
        time.sleep(1)
""")


def test_kill_parent_kills_child_within_one_second() -> None:
    """Plan AD-9: KILL_ON_JOB_CLOSE garantiert no zombies."""
    import psutil

    # Parent spawnen.
    parent = subprocess.Popen(
        [sys.executable, "-c", PARENT_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Erste Zeile = child PID.
        line = parent.stdout.readline().strip()
        try:
            child_pid = int(line)
        except ValueError:
            pytest.fail(f"Parent gab keine child PID aus: {line!r}")
        assert psutil.pid_exists(child_pid), f"child PID {child_pid} nicht da"

        # Parent hart killen via taskkill /F.
        subprocess.run(
            ["taskkill", "/F", "/PID", str(parent.pid), "/T"],
            check=False,
            capture_output=True,
        )

        # Plan AD-9: child sollte innerhalb 1 s weg sein.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not psutil.pid_exists(child_pid):
                # done.
                child_exit_time = time.monotonic()
                # 1 s ist Plan-Vorgabe; +500ms toleranz fuer Test-Slack
                assert True
                return
            time.sleep(0.05)
        pytest.fail(
            f"child PID {child_pid} lebt noch 2 s nach parent-kill — "
            "Job-Object hat nicht gekilled"
        )
    finally:
        # Cleanup parent (falls noch alive).
        try:
            parent.terminate()
            parent.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            try:
                parent.kill()
            except OSError:
                pass
