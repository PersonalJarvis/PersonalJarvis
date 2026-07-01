"""Integration tests for the desktop app's single-instance lock.

Focus: lock acquisition, re-entry denial, stale-PID takeover. The
actual ``webview.start()`` isn't headless-testable and is deliberately
not triggered here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jarvis.ui import desktop_app


@pytest.fixture
def lock_paths(tmp_path: Path) -> tuple[Path, Path]:
    """Isolated lock and meta paths per test, avoids interference with
    a real Jarvis installation on the same machine."""
    return tmp_path / "jarvis.lock", tmp_path / ".jarvis-running"


def test_first_acquire_succeeds(lock_paths: tuple[Path, Path]) -> None:
    lock_p, meta_p = lock_paths
    lock = desktop_app.acquire_single_instance_lock(
        lock_path=lock_p, meta_path=meta_p
    )
    try:
        assert lock.is_locked
    finally:
        lock.release()


def test_second_acquire_raises_when_first_alive(
    lock_paths: tuple[Path, Path],
) -> None:
    lock_p, meta_p = lock_paths

    first = desktop_app.acquire_single_instance_lock(
        lock_path=lock_p, meta_path=meta_p
    )
    try:
        # Meta sidecar with our own PID (guaranteed alive).
        meta_p.write_text(
            json.dumps({"pid": os.getpid(), "port": 47821, "started_at": 0.0}),
            encoding="utf-8",
        )
        with pytest.raises(desktop_app.SingleInstanceError):
            desktop_app.acquire_single_instance_lock(
                lock_path=lock_p, meta_path=meta_p
            )
    finally:
        first.release()


def test_release_frees_lock(lock_paths: tuple[Path, Path]) -> None:
    lock_p, meta_p = lock_paths

    first = desktop_app.acquire_single_instance_lock(
        lock_path=lock_p, meta_path=meta_p
    )
    first.release()

    second = desktop_app.acquire_single_instance_lock(
        lock_path=lock_p, meta_path=meta_p
    )
    try:
        assert second.is_locked
    finally:
        second.release()


def _find_dead_pid() -> int:
    """Returns a PID that, with very high probability, is not alive.

    Strategy: try 999999 downward — on modern Windows/Linux, PIDs are
    typically < 100000. psutil.pid_exists is O(1) per call.
    """
    import psutil  # type: ignore[import-not-found]

    for candidate in (999983, 999979, 999961, 999959):
        if not psutil.pid_exists(candidate):
            return candidate
    pytest.skip("No dead PID candidate found — system too full.")
    return 0  # unreachable


def test_stale_lock_gets_taken_over(lock_paths: tuple[Path, Path]) -> None:
    """If the sidecar names a dead PID, the second acquire is allowed to
    take over the lock (instead of raising SingleInstanceError)."""
    lock_p, meta_p = lock_paths

    dead_pid = _find_dead_pid()

    # Simulate a stale state: write the meta sidecar *without* the
    # FileLock actually being held — a process-crash scenario. Since
    # filelock uses POSIX/Windows OS locks, the lock is automatically
    # freed after the process exits; but the sidecar stays behind.
    meta_p.parent.mkdir(parents=True, exist_ok=True)
    meta_p.write_text(
        json.dumps({"pid": dead_pid, "port": 47821, "started_at": 0.0}),
        encoding="utf-8",
    )
    # The lock file may still exist too; that's fine — acquire handles it.
    lock_p.touch()

    lock = desktop_app.acquire_single_instance_lock(
        lock_path=lock_p, meta_path=meta_p
    )
    try:
        # Critical: _no_ SingleInstanceError despite the existing sidecar.
        assert lock.is_locked
    finally:
        lock.release()


def test_stale_lock_with_contention_cleans_sidecar(
    lock_paths: tuple[Path, Path],
) -> None:
    """Hard stale path: the OS lock is held by a subprocess that exits
    quickly. The sidecar names an independent dead PID (not the
    subprocess PID, otherwise it wouldn't be a stale case by
    definition). acquire must grab the lock after the subprocess exits
    and remove the sidecar.
    """
    import subprocess
    import sys as _sys

    lock_p, meta_p = lock_paths
    dead_pid = _find_dead_pid()

    # Subprocess that holds the lock briefly (250ms) then exits.
    holder_code = (
        "from filelock import FileLock;"
        "import time;"
        f"l = FileLock(r'{lock_p}');"
        "l.acquire();"
        "print('LOCKED', flush=True);"
        "time.sleep(0.25);"
        "l.release();"
    )
    proc = subprocess.Popen(
        [_sys.executable, "-c", holder_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait until the holder actually holds the lock.
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert b"LOCKED" in line, f"Holder subprocess did not print LOCKED: {line!r}"

    # Sidecar with a *different* dead PID (not proc.pid!) — otherwise
    # _pid_alive(proc.pid) would return True as long as the subprocess
    # runs, and we'd be on the "Jarvis is already running" path instead
    # of the stale path.
    meta_p.parent.mkdir(parents=True, exist_ok=True)
    meta_p.write_text(
        json.dumps({"pid": dead_pid, "port": 47821, "started_at": 0.0}),
        encoding="utf-8",
    )

    # Now acquire: the first attempt (timeout=0) fails -> sidecar read
    # -> PID dead -> delete sidecar -> retry (timeout=2s) -> succeeds once
    # the subprocess releases.
    lock = desktop_app.acquire_single_instance_lock(
        lock_path=lock_p, meta_path=meta_p
    )
    try:
        assert lock.is_locked
        assert not meta_p.exists(), (
            "Sidecar with a dead PID must be removed on the stale path."
        )
    finally:
        lock.release()
        proc.wait(timeout=3)
