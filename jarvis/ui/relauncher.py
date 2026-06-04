"""Detached helper that restarts the Jarvis desktop app cleanly.

The desktop app cannot restart itself in-process: its single-instance Named
Mutex (``Global\\PersonalJarvis_v1``) is held until the process exits, and a
fresh launcher started while the old process still lives would just activate the
old window and exit (see ``jarvis/ui/shell/single_instance.py``). So a restart is
two-phase:

1. The dying app spawns THIS detached helper (``DesktopApp.request_restart``).
2. The helper waits for the old PID to disappear (the kernel releases the mutex
   on exit), then starts a fresh launcher that claims the now-free mutex.

Invoked as::

    python -m jarvis.ui.relauncher <parent_pid> <repo_cwd>

It runs windowless + detached so it outlives its parent. Stdlib only — it must
start fast and never pull the heavy ``jarvis`` runtime into a throwaway process.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

LAUNCHER_MODULE = "jarvis.ui.web.launcher"


def build_launch_command(executable: str) -> list[str]:
    """Argv that boots a fresh desktop app with the same interpreter."""
    return [executable, "-m", LAUNCHER_MODULE]


def detached_creationflags() -> int:
    """Windows creationflags that make a child outlive its parent, windowless.

    ``DETACHED_PROCESS`` cuts the child loose from the parent's console/process
    group; ``CREATE_NO_WINDOW`` keeps ``pythonw`` from flashing a console. ``0``
    on non-Windows (the caller uses ``start_new_session`` there instead).
    """
    if sys.platform == "win32":
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return detached | no_window
    return 0


def pid_alive(pid: int) -> bool:
    """True if a process with ``pid`` currently exists. Never kills it.

    ``os.kill(pid, 0)`` is POSIX-safe (signal 0 only probes) but on Windows it
    routes to ``TerminateProcess`` for non-CTRL signals — so on Windows we probe
    with ``OpenProcess``/``WaitForSingleObject`` instead.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102  # still running; WAIT_OBJECT_0 (0) = exited
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False  # already gone (or no rights — treat as gone)
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    return True


def wait_for_pid_exit(
    pid: int,
    *,
    timeout: float = 45.0,
    poll: float = 0.15,
    _alive=pid_alive,
    _now=time.monotonic,
    _sleep=time.sleep,
) -> bool:
    """Block until ``pid`` is gone (True) or ``timeout`` elapses (False)."""
    deadline = _now() + timeout
    while _now() < deadline:
        if not _alive(pid):
            return True
        _sleep(poll)
    return not _alive(pid)


def main(
    argv: list[str] | None = None,
    *,
    _wait=wait_for_pid_exit,
    _spawn=subprocess.Popen,
    _sleep=time.sleep,
) -> int:
    """Wait for the old app to exit, then start a fresh launcher.

    Returns ``2`` on bad argv, ``0`` once the new launcher has been spawned.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        return 2
    try:
        pid = int(argv[0])
    except ValueError:
        return 2
    cwd = argv[1]

    _wait(pid, timeout=45.0)
    # Extra grace so the kernel finishes releasing the mutex + the TCP port
    # before the new launcher tries to claim them.
    _sleep(1.0)

    kwargs: dict[str, object] = {"cwd": cwd, "close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = detached_creationflags()
    else:
        kwargs["start_new_session"] = True
    _spawn(build_launch_command(sys.executable), **kwargs)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
