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


def run_restart_quit_sequence(
    *,
    set_quit,
    destroy_window,
    pre_delay: float = 0.15,
    hard_exit_after: float = 0.7,
    _sleep=time.sleep,
    _exit=os._exit,
) -> None:
    """Quit the DYING app for a restart, hard-exiting if shutdown stalls.

    Runs in a daemon thread of the app being replaced. It (1) waits a beat so
    the HTTP 200 reaches the frontend, (2) marks the quit + destroys the window
    (the normal clean-shutdown path), then (3) **force-exits the process** if it
    is still alive after ``hard_exit_after`` seconds.

    The hard exit is the load-bearing part: the relauncher's fresh instance can
    only claim the single-instance mutex + TCP port once THIS process is gone.
    A stalled shutdown — or a cross-thread ``window.destroy`` that fails to
    unblock the GUI loop (the BUG-031 hazard) — would otherwise leave a
    windowless process holding the lock, and the new instance would bounce off
    it: the "shuts down but never comes back" bug. If the normal shutdown
    finishes first, the main thread exits the process and this daemon thread
    dies before reaching ``_exit`` — so the force-exit only fires when it must.

    Speed note (2026-06-21): for a RESTART the dying app does not need a full,
    leisurely clean shutdown — the fresh instance re-initialises every subsystem
    anyway. So the hard-exit cap is tight (2 s, was 10 s): a slow or hanging
    teardown (MCP session close, the BUG-031 window-destroy hang) is force-exited
    fast, freeing the lock + port for the fresh, fast-booting instance. The only
    cost is some teardown skipped on restart (e.g. an MCP subprocess re-spawned
    by the new instance) — acceptable for a controlled restart.
    """
    _sleep(pre_delay)
    try:
        set_quit()
    except Exception:  # noqa: BLE001 — never block the quit on a callback error
        pass
    try:
        destroy_window()
    except Exception:  # noqa: BLE001 — destroy may already be impossible; force-exit anyway
        pass
    _sleep(hard_exit_after)
    _exit(0)


def _new_instance_settled(
    pid,
    *,
    _alive=pid_alive,
    _sleep=time.sleep,
    checks: int = 5,
    interval: float = 1.0,
) -> bool:
    """True if a freshly spawned launcher is still alive after a short grace.

    A secondary that bounces off the single-instance lock prints "already
    running", focuses the existing window, and exits within ~1 s; a real primary
    keeps running. So "still alive after a few seconds" is a good proxy for "the
    new instance actually came up". An unverifiable pid (missing/invalid) is
    treated as success to avoid spinning needlessly.
    """
    if not isinstance(pid, int) or pid <= 0:
        return True
    for _ in range(checks):
        _sleep(interval)
        if not _alive(pid):
            return False
    return True


def main(
    argv: list[str] | None = None,
    *,
    _wait=wait_for_pid_exit,
    _spawn=subprocess.Popen,
    _sleep=time.sleep,
    _alive=pid_alive,
    _settled=_new_instance_settled,
    attempts: int = 3,
) -> int:
    """Wait for the old app to exit, then start a fresh launcher — verified.

    The single-instance lock frees only once the old process is gone, so we
    wait for that first. After spawning the new launcher we verify it actually
    stayed up (it would otherwise have bounced off a still-held lock) and retry
    a couple of times if not.

    Returns ``2`` on bad argv, ``0`` once a new instance is confirmed up, ``1``
    if every spawn attempt failed to bring one up.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        return 2
    try:
        pid = int(argv[0])
    except ValueError:
        return 2
    cwd = argv[1]

    kwargs: dict[str, object] = {"cwd": cwd, "close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = detached_creationflags()
    else:
        kwargs["start_new_session"] = True

    for attempt in range(attempts):
        # Never launch into a still-held lock: the old process must be gone.
        if _alive(pid):
            _wait(pid, timeout=45.0 if attempt == 0 else 15.0)
        # Extra grace so the kernel finishes releasing the mutex + the TCP port
        # before the new launcher tries to claim them. Short — the kernel frees
        # both the instant the old PID is gone; this only covers the tail.
        _sleep(0.2)

        proc = _spawn(build_launch_command(sys.executable), **kwargs)
        new_pid = getattr(proc, "pid", None)
        if _settled(new_pid, _alive=_alive, _sleep=_sleep):
            return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
