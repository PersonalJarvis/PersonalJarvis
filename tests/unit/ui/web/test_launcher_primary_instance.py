"""Headless launcher must claim primary-instance status via the lock.

Root cause of the 94 `crash_recovery` false-negatives (live forensic
2026-05-31, missions 019e6fea / 019e7095): a headless Jarvis run NEVER set
``JARVIS_PRIMARY_INSTANCE``, so ``server.py:_init_mission_stack`` defaulted it
to ``"1"`` (primary) and the headless boot ran ``startup_recover`` against the
shared ``missions.db`` — sweeping the DESKTOP instance's actively-running
missions to ``FAILED('crash_recovery')``.

The fix makes headless decide primary status the same way the desktop path
does: whoever holds the single-instance lock is primary and may run the
sweep. A headless run that is the SOLE instance (the €5-VPS case) holds the
lock and stays primary; a parallel/secondary headless run (lock already held
by the desktop app or another run) marks itself NON-primary and must not
sweep — but still boots (headless is meant to coexist with a primary for
tests / parallel dev / smoke probes).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.ui.desktop_app import acquire_single_instance_lock
from jarvis.ui.web.launcher import (
    _acquire_primary_lock_for_headless,
    _claim_headless_primary_lock,
)


def test_sole_headless_instance_is_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A headless run that is the ONLY instance holds the lock and IS primary,
    so it still recovers genuinely orphaned missions (the VPS case)."""
    monkeypatch.delenv("JARVIS_PRIMARY_INSTANCE", raising=False)
    lock_path = tmp_path / "jarvis.lock"
    meta_path = tmp_path / "jarvis.lock.meta"

    lock = _acquire_primary_lock_for_headless(
        lock_path=lock_path, meta_path=meta_path
    )
    try:
        assert lock is not None, "sole instance must acquire the lock"
        import os

        assert os.environ["JARVIS_PRIMARY_INSTANCE"] == "1"
    finally:
        if lock is not None:
            lock.release()


def test_secondary_headless_instance_is_not_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When another (live) instance already holds the lock, a headless run must
    mark itself NON-primary so its boot does NOT sweep the primary's live
    missions to crash_recovery — but it must still return (boot continues)."""
    monkeypatch.delenv("JARVIS_PRIMARY_INSTANCE", raising=False)
    lock_path = tmp_path / "jarvis.lock"
    meta_path = tmp_path / "jarvis.lock.meta"

    # Simulate the desktop instance already holding the lock in THIS (live)
    # process — the meta PID is our own, so stale-detection sees it alive.
    held = acquire_single_instance_lock(
        lock_path=lock_path, meta_path=meta_path
    )
    try:
        lock = _acquire_primary_lock_for_headless(
            lock_path=lock_path, meta_path=meta_path
        )
        assert lock is None, "secondary instance must not acquire the lock"
        import os

        assert os.environ["JARVIS_PRIMARY_INSTANCE"] == "0"
    finally:
        held.release()


def test_no_lock_headless_instance_is_secondary_and_does_not_hold_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A --no-lock headless/dev/smoke run must not block the desktop app.

    It is explicitly secondary, so it does not run crash recovery and it leaves
    the single-instance lock free for a real desktop autostart.
    """
    monkeypatch.delenv("JARVIS_PRIMARY_INSTANCE", raising=False)
    lock_path = tmp_path / "jarvis.lock"
    meta_path = tmp_path / "jarvis.lock.meta"

    lock = _claim_headless_primary_lock(
        SimpleNamespace(no_lock=True),
        lock_path=lock_path,
        meta_path=meta_path,
    )
    assert lock is None

    import os

    assert os.environ["JARVIS_PRIMARY_INSTANCE"] == "0"

    desktop_lock = acquire_single_instance_lock(
        lock_path=lock_path, meta_path=meta_path
    )
    desktop_lock.release()
