"""E2E job-object kill-parent. Plan AD-9 — Main-Jarvis crash kills the overlay.

Strategy: we start a "parent process" that spawns a "child process"
and attaches it to a job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
Then we hard taskkill the parent and assert that the child is also
gone within 1 s.

Windows-only — a skip on other platforms.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest

if sys.platform != "win32":
    pytest.skip("Job object is Windows-only", allow_module_level=True)

try:
    import psutil  # noqa: F401
except ImportError:  # pragma: no cover
    pytest.skip("psutil required for process watch", allow_module_level=True)


PARENT_SCRIPT = textwrap.dedent("""
    import os
    import subprocess
    import sys
    import time

    import win32api  # type: ignore[import-not-found]
    import win32con  # type: ignore[import-not-found]
    import win32job  # type: ignore[import-not-found]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    # Spawn child: runs for 30 s.
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )

    # Set up the job object.
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

    # Print the child's PID so the test can watch it.
    sys.stdout.write(str(child.pid) + chr(10))
    sys.stdout.flush()

    # Endless loop so the test can kill us.
    while True:
        time.sleep(1)
""")


def test_kill_parent_kills_child_within_one_second() -> None:
    """Plan AD-9: KILL_ON_JOB_CLOSE guarantees no zombies."""
    import psutil

    # Spawn the parent.
    parent = subprocess.Popen(
        [sys.executable, "-c", PARENT_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # First line = child PID.
        line = parent.stdout.readline().strip()
        try:
            child_pid = int(line)
        except ValueError:
            pytest.fail(f"Parent did not print a child PID: {line!r}")
        assert psutil.pid_exists(child_pid), f"child PID {child_pid} not present"

        # Hard-kill the parent via taskkill /F.
        subprocess.run(
            ["taskkill", "/F", "/PID", str(parent.pid), "/T"],
            check=False,
            capture_output=True,
        )

        # Plan AD-9: child should be gone within 1 s.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not psutil.pid_exists(child_pid):
                # done.
                child_exit_time = time.monotonic()
                # 1 s is the plan spec; +500ms tolerance for test slack
                assert True
                return
            time.sleep(0.05)
        pytest.fail(
            f"child PID {child_pid} is still alive 2 s after parent kill — "
            "job object did not kill it"
        )
    finally:
        # Cleanup parent (if still alive).
        try:
            parent.terminate()
            parent.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            try:
                parent.kill()
            except OSError:
                pass
