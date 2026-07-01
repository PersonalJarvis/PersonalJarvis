"""Smoke test Phase 6 / Prompt 2: Windows Job Object reaping verification.

Verifies the core guarantee from ADR-0009 §3 + research doc §C: closing
the job-object handle atomically kills the entire descendant tree of the
assigned subprocess — no zombie, no orphan.

Workflow:

1. Platform gate: Windows only. Otherwise `[SKIP]` + exit 0.
2. Open `WindowsJobObject('jobkill-test')` as an async context manager.
3. Spawn child: `python -c "..."`, which forks a grandchild
   `python -c "time.sleep"` and prints its PID to stdout. Both run ~60 s.
4. Parse the child PID via `proc.pid`, the grandchild PID via a stdout read.
5. Assign both PIDs to the `WindowsJobObject` (grandchild possibly later —
   we assign all fresh descendants defensively).
6. Close the job-object handle (via `await job.close()` OR context exit).
7. 1 s settle pause.
8. Assert via `psutil.pid_exists(pid)` that both PIDs == False.

Exit 0 on success OR on `[SKIP]`. Exit 1 only on real failures.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

# Repo root in sys.path so `from jarvis.missions...` works
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK = "[OK]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"


# Inline Python: spawns a grandchild, prints its PID, then sleeps 60 s itself.
_CHILD_SCRIPT = (
    "import os, sys, subprocess, time;"
    "p = subprocess.Popen([sys.executable, '-c', "
    "'import time; time.sleep(60)']);"
    "sys.stdout.write(str(p.pid) + chr(10));"
    "sys.stdout.flush();"
    "time.sleep(60)"
)


async def _spawn_child_with_grandchild() -> tuple[subprocess.Popen[str], int]:
    """Spawns child + parses the grandchild PID from stdout (line 1).

    Returns:
        (child_process, grandchild_pid)
    """
    creationflags = 0
    if sys.platform == "win32":
        # So the job-object assign isn't caught off guard by inheritance
        # shenanigans, give the child its own process group.
        creationflags = (
            subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | subprocess.CREATE_BREAKAWAY_FROM_JOB  # type: ignore[attr-defined]
        )

    proc = subprocess.Popen(  # noqa: S603 — args are controlled, no shell
        [sys.executable, "-c", _CHILD_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        creationflags=creationflags,
    )

    # Read first line in a thread-safe way (Popen.stdout is sync).
    loop = asyncio.get_running_loop()
    assert proc.stdout is not None
    grandchild_line = await loop.run_in_executor(None, proc.stdout.readline)
    grandchild_pid = int(grandchild_line.strip())
    return proc, grandchild_pid


async def smoke() -> int:
    failures: list[str] = []

    # --- Section 1: platform gate ---
    if sys.platform != "win32":
        print(f"{SKIP} not Windows — job-object kill test skipped")
        return 0
    print(f"{OK} platform=win32, job-object test running")

    # --- Section 2: psutil available? ---
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        print(f"{SKIP} psutil not installed — `pip install psutil`")
        return 0
    print(f"{OK} psutil {psutil.__version__} imported")

    from jarvis.missions.isolation import WindowsJobObject  # noqa: PLC0415

    # --- Section 3+4: open job object, spawn child + grandchild ---
    child_pid: int | None = None
    grandchild_pid: int | None = None

    async with WindowsJobObject("smoke-phase6-jobkill") as job:
        print(f"{OK} WindowsJobObject opened (closed={job.closed})")

        proc, grandchild_pid = await _spawn_child_with_grandchild()
        child_pid = proc.pid
        print(f"{OK} child spawned pid={child_pid}, grandchild pid={grandchild_pid}")

        # --- Section 5: assign both PIDs ---
        try:
            job.assign(child_pid)
            print(f"{OK} job.assign(child={child_pid})")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"job.assign(child) failed: {exc}")

        try:
            job.assign(grandchild_pid)
            print(f"{OK} job.assign(grandchild={grandchild_pid})")
        except Exception as exc:  # noqa: BLE001
            # A grandchild assign can legitimately fail (already assigned
            # via job inheritance, or we don't have the handle via the
            # process group). What matters is: KILL_ON_JOB_CLOSE still
            # kills all descendants regardless.
            print(f"{SKIP} job.assign(grandchild) warning: {exc}")

        # Sanity: both PIDs are still ALIVE
        if not psutil.pid_exists(child_pid):
            failures.append(f"child pid={child_pid} already dead before close")
        if not psutil.pid_exists(grandchild_pid):
            failures.append(f"grandchild pid={grandchild_pid} already dead before close")
        if not failures:
            print(f"{OK} pre-close: both PIDs alive")

    # --- Section 6+7: job handle is now closed, wait briefly ---
    print(f"{OK} job context exited (handle closed)")
    await asyncio.sleep(1.0)

    # --- Section 8: both PIDs must be dead ---
    if child_pid is not None and psutil.pid_exists(child_pid):
        # Defensive: could have been recycled.
        try:
            p = psutil.Process(child_pid)
            if "python" in p.name().lower():
                failures.append(f"child pid={child_pid} still alive after job.close()")
            else:
                print(f"{OK} child pid={child_pid} recycled, original dead")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            print(f"{OK} child pid={child_pid} no longer reachable")
    else:
        print(f"{OK} child pid={child_pid} reaped by Job Object")

    if grandchild_pid is not None and psutil.pid_exists(grandchild_pid):
        try:
            p = psutil.Process(grandchild_pid)
            if "python" in p.name().lower():
                failures.append(
                    f"grandchild pid={grandchild_pid} still alive after job.close()"
                )
            else:
                print(f"{OK} grandchild pid={grandchild_pid} recycled, original dead")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            print(f"{OK} grandchild pid={grandchild_pid} no longer reachable")
    else:
        print(f"{OK} grandchild pid={grandchild_pid} reaped by Job Object")

    print()
    if failures:
        print(f"{FAIL} {len(failures)} smoke failures:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"{OK} ALL SMOKE CHECKS GREEN -- Job-Object reaping guaranteed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke()))
