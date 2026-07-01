"""Contract test for `acquire_single_instance_lock`.

Uses isolated temp paths so parallel test runs don't block each other —
the prod paths live under `DATA_DIR` and are reserved for real Jarvis
instances.
"""
from __future__ import annotations

import json
import os

import pytest

from jarvis.ui.desktop_app import (
    SingleInstanceError,
    acquire_single_instance_lock,
)


def test_first_claim_succeeds(tmp_path) -> None:
    lock_file = tmp_path / "jarvis.lock"
    meta_file = tmp_path / ".jarvis-running"

    lock = acquire_single_instance_lock(
        timeout=0.0, lock_path=lock_file, meta_path=meta_file
    )
    try:
        assert lock.is_locked
    finally:
        lock.release()


def test_second_claim_raises_when_first_alive(tmp_path) -> None:
    lock_file = tmp_path / "jarvis.lock"
    meta_file = tmp_path / ".jarvis-running"

    first = acquire_single_instance_lock(
        timeout=0.0, lock_path=lock_file, meta_path=meta_file
    )
    # Write the meta file with our PID (our process is guaranteed to be alive)
    meta_file.write_text(
        json.dumps({"pid": os.getpid(), "port": 47821, "started_at": 0}),
        encoding="utf-8",
    )
    try:
        with pytest.raises(SingleInstanceError):
            acquire_single_instance_lock(
                timeout=0.0, lock_path=lock_file, meta_path=meta_file
            )
    finally:
        first.release()


def test_live_but_portless_holder_is_evicted(tmp_path, monkeypatch) -> None:
    """Forensic 2026-06-26: a LIVE but non-serving "lock-zombie" (its webserver
    socket died on WinError 64, but voice/telegram kept the process alive) held
    the lock with no port and no window, so every restart bounced. A live holder
    whose port does NOT answer health must be evicted so the boot can proceed."""
    lock_file = tmp_path / "jarvis.lock"
    meta_file = tmp_path / ".jarvis-running"

    from filelock import FileLock

    holder = FileLock(str(lock_file))
    holder.acquire(timeout=0.0)
    meta_file.write_text(
        json.dumps({"pid": 424242, "port": 47821, "started_at": 0}),
        encoding="utf-8",
    )
    # Pretend the zombie pid is alive (it is — that is the whole trap).
    monkeypatch.setattr(
        "jarvis.ui.desktop_app._pid_alive", lambda pid: pid == 424242
    )

    killed: dict[str, int] = {}

    def fake_terminate(pid: int) -> bool:
        killed["pid"] = pid
        holder.release()  # simulate the zombie dying → OS frees the lock
        return True

    fresh = acquire_single_instance_lock(
        timeout=0.0,
        lock_path=lock_file,
        meta_path=meta_file,
        health_probe=lambda port: False,  # dead port → zombie
        terminate=fake_terminate,
    )
    try:
        assert killed["pid"] == 424242, "the non-serving zombie must be terminated"
        assert fresh.is_locked, "the fresh boot must reclaim the lock"
    finally:
        fresh.release()


def test_live_holder_with_responsive_port_is_respected(tmp_path) -> None:
    """A live holder whose port ANSWERS health is a real running instance — it
    must NOT be evicted (anti-false-positive: never kill a healthy Jarvis)."""
    lock_file = tmp_path / "jarvis.lock"
    meta_file = tmp_path / ".jarvis-running"

    first = acquire_single_instance_lock(
        timeout=0.0, lock_path=lock_file, meta_path=meta_file
    )
    meta_file.write_text(
        json.dumps({"pid": os.getpid(), "port": 47821, "started_at": 0}),
        encoding="utf-8",
    )

    def must_not_terminate(pid: int) -> bool:  # pragma: no cover - must never run
        raise AssertionError("a healthy holder must never be terminated")

    try:
        with pytest.raises(SingleInstanceError):
            acquire_single_instance_lock(
                timeout=0.0,
                lock_path=lock_file,
                meta_path=meta_file,
                health_probe=lambda port: True,  # port answers → healthy
                terminate=must_not_terminate,
            )
    finally:
        first.release()


def test_live_self_pid_holder_is_never_terminated(tmp_path) -> None:
    """Defense: even with a dead port, the holder pid == our own pid must never
    be evicted (no suicide). It is treated as a live instance instead."""
    lock_file = tmp_path / "jarvis.lock"
    meta_file = tmp_path / ".jarvis-running"

    first = acquire_single_instance_lock(
        timeout=0.0, lock_path=lock_file, meta_path=meta_file
    )
    meta_file.write_text(
        json.dumps({"pid": os.getpid(), "port": 47821, "started_at": 0}),
        encoding="utf-8",
    )

    def must_not_terminate(pid: int) -> bool:  # pragma: no cover - must never run
        raise AssertionError("self pid must never be terminated")

    try:
        with pytest.raises(SingleInstanceError):
            acquire_single_instance_lock(
                timeout=0.0,
                lock_path=lock_file,
                meta_path=meta_file,
                health_probe=lambda port: False,  # dead port, but it's US
                terminate=must_not_terminate,
            )
    finally:
        first.release()


def test_stale_lock_is_reclaimed(tmp_path) -> None:
    """If the PID in the meta file is dead, a new process may take over."""
    lock_file = tmp_path / "jarvis.lock"
    meta_file = tmp_path / ".jarvis-running"

    # Simulate a stale lock: meta file with a guaranteed-dead PID (0).
    # (filelock alone isn't enough here — we also need to simulate it as held.)
    from filelock import FileLock

    stale = FileLock(str(lock_file))
    stale.acquire(timeout=0.0)
    meta_file.write_text(
        json.dumps({"pid": 0, "port": 47821, "started_at": 0}), encoding="utf-8"
    )

    # Release the lock again, so the stale detection can kick in.
    # (The prod case would be: a dead holder held the lock and the kernel
    # frees it — in the test we simulate this with a manual release.)
    stale.release()

    # A new claim must go through (PID 0 is never a real user process).
    fresh = acquire_single_instance_lock(
        timeout=0.0, lock_path=lock_file, meta_path=meta_file
    )
    try:
        assert fresh.is_locked
    finally:
        fresh.release()
