"""Contract-Test für `acquire_single_instance_lock`.

Verwendet isolierte Temp-Paths damit parallele Testläufe sich nicht gegenseitig
blockieren — die Prod-Paths liegen unter `DATA_DIR` und sind für echte Jarvis-
Instanzen reserviert.
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
    # Meta-File mit unserem PID schreiben (unser Prozess ist garantiert alive)
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


def test_stale_lock_is_reclaimed(tmp_path) -> None:
    """Wenn der PID im Meta-File tot ist, darf ein neuer Prozess übernehmen."""
    lock_file = tmp_path / "jarvis.lock"
    meta_file = tmp_path / ".jarvis-running"

    # Simuliere stale lock: Meta-File mit garantiert-toter PID (0).
    # (filelock allein genügt hier nicht — wir müssen es auch belegt simulieren.)
    from filelock import FileLock

    stale = FileLock(str(lock_file))
    stale.acquire(timeout=0.0)
    meta_file.write_text(
        json.dumps({"pid": 0, "port": 47821, "started_at": 0}), encoding="utf-8"
    )

    # Lock wieder freigeben, damit die Stale-Detection greifen kann.
    # (Prod-Fall wäre: gestorbener Halter hat Lock gehalten und Kernel gibt
    # es frei — im Test simulieren wir mit manuellem release.)
    stale.release()

    # Neuer Claim muss durchkommen (PID 0 ist nie ein echter User-Prozess).
    fresh = acquire_single_instance_lock(
        timeout=0.0, lock_path=lock_file, meta_path=meta_file
    )
    try:
        assert fresh.is_locked
    finally:
        fresh.release()
