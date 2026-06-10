"""VaultLock cross-process staleness uses the WALL clock (Wave-2 B4).

The lock file is read by OTHER processes, potentially after a reboot. A
``time.monotonic()`` timestamp in the file is meaningless across process
restarts (the monotonic clock restarts near zero per boot), so a stale lock
from a crashed previous boot could look "fresh" — or "from the future" —
forever. The contract pinned here: the FILE carries ``time.time()`` (wall
clock); the in-process acquire deadline loop may keep monotonic.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from jarvis.memory.wiki.lock import VaultLock


def _write_lock(path: Path, ts: float, *, pid: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid if pid is not None else os.getpid()};{ts}", encoding="utf-8")


def test_lock_file_carries_wall_clock_timestamp(tmp_path: Path) -> None:
    lock = VaultLock(tmp_path / "curator.lock")
    assert lock.acquire(timeout_s=0.1) is True
    try:
        content = (tmp_path / "curator.lock").read_text(encoding="utf-8")
        _pid, ts = content.split(";", 1)
        # Wall clock, not a small monotonic uptime counter.
        assert abs(float(ts) - time.time()) < 60.0
    finally:
        lock.release()


def test_stale_lock_from_previous_boot_is_stolen(tmp_path: Path) -> None:
    """A wall-clock timestamp older than stale_after is stolen."""
    path = tmp_path / "curator.lock"
    _write_lock(path, time.time() - 9999.0)
    lock = VaultLock(path, stale_after_seconds=300)
    assert lock.acquire(timeout_s=0.1) is True
    lock.release()


def test_fresh_lock_is_not_stolen(tmp_path: Path) -> None:
    path = tmp_path / "curator.lock"
    _write_lock(path, time.time())
    lock = VaultLock(path, stale_after_seconds=300)
    assert lock.acquire(timeout_s=0.1) is False
    assert path.exists()


def test_future_timestamp_is_treated_as_corrupt_and_stolen(tmp_path: Path) -> None:
    """A far-future timestamp is a pre-fix monotonic remnant — steal it."""
    path = tmp_path / "curator.lock"
    _write_lock(path, time.time() + 9999.0)
    lock = VaultLock(path, stale_after_seconds=300)
    assert lock.acquire(timeout_s=0.1) is True
    lock.release()


def test_release_is_idempotent_and_context_manager_works(tmp_path: Path) -> None:
    path = tmp_path / "curator.lock"
    lock = VaultLock(path)
    with lock:
        assert path.exists()
    assert not path.exists()
    lock.release()  # second release is a no-op
