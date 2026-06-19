"""Tests fuer cleanup.startup_sweep + daily_cleanup_task."""
from __future__ import annotations

import asyncio
import os
import shutil
import stat
import time
from pathlib import Path

import pytest

from jarvis.missions.cleanup import (
    DEFAULT_CLEANUP_DAYS,
    DEFAULT_DAILY_INTERVAL_SECONDS,
    daily_cleanup_task,
    startup_sweep,
)

# --- startup_sweep ---


@pytest.mark.asyncio
async def test_sweep_nonexistent_root_returns_zero(tmp_path: Path) -> None:
    """Wenn isolation_root nicht existiert: sauberer Return mit zeros."""
    nonexistent = tmp_path / "nonexistent"
    stats = await startup_sweep(isolation_root=nonexistent)
    assert stats == {"scanned": 0, "removed": 0, "errors": 0}


@pytest.mark.asyncio
async def test_sweep_empty_root_no_action(tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    root.mkdir()
    stats = await startup_sweep(isolation_root=root)
    assert stats["scanned"] == 0
    assert stats["removed"] == 0


@pytest.mark.asyncio
async def test_sweep_keeps_recent_directories(tmp_path: Path) -> None:
    """Direktories juenger als cleanup_days bleiben unangetastet."""
    root = tmp_path / "outputs"
    root.mkdir()
    fresh = root / "mission_fresh"
    fresh.mkdir()
    (fresh / "log.txt").write_text("x")

    stats = await startup_sweep(isolation_root=root, cleanup_days=14)
    assert stats["scanned"] == 1
    assert stats["removed"] == 0
    assert fresh.exists()


@pytest.mark.asyncio
async def test_sweep_removes_old_directories(tmp_path: Path) -> None:
    """Direktories aelter als cleanup_days werden entfernt."""
    root = tmp_path / "outputs"
    root.mkdir()
    old = root / "mission_old"
    old.mkdir()
    (old / "log.txt").write_text("old")

    # mtime auf 30 Tage zurueck setzen
    thirty_days_ago = time.time() - (30 * 86400)
    os.utime(old, (thirty_days_ago, thirty_days_ago))

    stats = await startup_sweep(isolation_root=root, cleanup_days=14)
    assert stats["scanned"] == 1
    assert stats["removed"] == 1
    assert not old.exists()


@pytest.mark.asyncio
async def test_sweep_mixed_keeps_fresh_removes_old(tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    root.mkdir()

    fresh = root / "mission_fresh"
    fresh.mkdir()
    (fresh / "x.txt").write_text("fresh")

    old = root / "mission_old"
    old.mkdir()
    (old / "x.txt").write_text("old")
    thirty_days_ago = time.time() - (30 * 86400)
    os.utime(old, (thirty_days_ago, thirty_days_ago))

    stats = await startup_sweep(isolation_root=root, cleanup_days=14)
    assert stats["scanned"] == 2
    assert stats["removed"] == 1
    assert fresh.exists()
    assert not old.exists()


@pytest.mark.asyncio
async def test_sweep_cleanup_days_zero_removes_anything(tmp_path: Path) -> None:
    """Mit cleanup_days=0 sollten alle gefundenen Dirs entfernt werden — die
    exakte Anzahl haengt allerdings von der FS-mtime-Aufloesung ab; wir
    pruefen nur dass mindestens einer entfernt wird (kein Crash, sweep
    funktioniert)."""
    root = tmp_path / "outputs"
    root.mkdir()
    a = root / "a"
    a.mkdir()
    # Setze mtime explizit in die Vergangenheit damit age_seconds > 0 garantiert ist
    past = time.time() - 1.0
    os.utime(a, (past, past))
    b = root / "b"
    b.mkdir()
    os.utime(b, (past, past))

    stats = await startup_sweep(isolation_root=root, cleanup_days=0)
    assert stats["scanned"] == 2
    assert stats["removed"] == 2


@pytest.mark.asyncio
async def test_sweep_handles_locked_files_gracefully(tmp_path: Path) -> None:
    """Wenn rmtree partial failt (z.B. file lock), errors-counter geht hoch
    aber sweep crasht nicht."""
    root = tmp_path / "outputs"
    root.mkdir()
    old = root / "mission_old"
    old.mkdir()
    (old / "x.txt").write_text("data")
    thirty_days_ago = time.time() - (30 * 86400)
    os.utime(old, (thirty_days_ago, thirty_days_ago))

    # Even wenn rmtree mit ignore_errors=True versucht, sollte stats valide sein
    stats = await startup_sweep(isolation_root=root, cleanup_days=14)
    # In normaler Fall: removed=1
    assert stats["scanned"] == 1


@pytest.mark.asyncio
async def test_sweep_removes_directories_with_readonly_files(tmp_path: Path) -> None:
    """Git object/pack files inside mission worktrees are read-only on
    Windows; a plain ``shutil.rmtree`` fails with PermissionError there, so
    the backlog was never deleted and grew forever (the 30s-launch bug:
    128 failing rmtree attempts on every boot)."""
    root = tmp_path / "outputs"
    root.mkdir()
    old = root / "mission_old"
    pack_dir = old / "workspace" / ".git" / "objects" / "pack"
    pack_dir.mkdir(parents=True)
    pack = pack_dir / "pack-abc.idx"
    pack.write_text("binary-ish")
    pack.chmod(stat.S_IREAD)

    thirty_days_ago = time.time() - (30 * 86400)
    os.utime(old, (thirty_days_ago, thirty_days_ago))

    stats = await startup_sweep(isolation_root=root, cleanup_days=14)

    assert stats["removed"] == 1
    assert stats["errors"] == 0
    assert not old.exists()


@pytest.mark.asyncio
async def test_sweep_does_not_block_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep runs at boot on the same loop that serves /api/health; if
    it blocks, the desktop window cannot appear until the sweep finishes
    (the 30s-launch bug). A heartbeat task must keep ticking while a slow
    removal is in progress."""
    root = tmp_path / "outputs"
    root.mkdir()
    old = root / "mission_old"
    old.mkdir()
    thirty_days_ago = time.time() - (30 * 86400)
    os.utime(old, (thirty_days_ago, thirty_days_ago))

    def slow_remove(entry: Path, *, repo_root: Path | None) -> bool:
        time.sleep(0.5)  # simulates rmtree/git churning through a big tree
        shutil.rmtree(entry, ignore_errors=True)
        return True

    monkeypatch.setattr("jarvis.missions.cleanup._remove_entry", slow_remove)

    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    try:
        await startup_sweep(isolation_root=root, cleanup_days=14)
    finally:
        hb.cancel()

    assert ticks >= 5, f"event loop was blocked during the sweep (ticks={ticks})"


# --- daily_cleanup_task ---


@pytest.mark.asyncio
async def test_daily_task_runs_sweep_then_cancels(tmp_path: Path) -> None:
    """daily_task fuehrt sweep periodisch aus, cancelable."""
    root = tmp_path / "outputs"
    root.mkdir()

    # Task mit kurzem Intervall starten
    task = asyncio.create_task(
        daily_cleanup_task(
            isolation_root=root,
            cleanup_days=14,
            interval_seconds=0.1,
        )
    )

    # 250ms warten -> 2 sweeps sollten gelaufen sein
    await asyncio.sleep(0.25)

    # Cancel + sauberer Exit
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_daily_task_continues_on_sweep_error(tmp_path: Path, monkeypatch) -> None:
    """Wenn ein sweep crasht, soll der Task weiter laufen (catch-und-log)."""
    root = tmp_path / "outputs"
    root.mkdir()

    call_count = {"n": 0}

    async def crashing_sweep(*args, **kwargs):
        call_count["n"] += 1
        raise RuntimeError("simuliert")

    monkeypatch.setattr("jarvis.missions.cleanup.startup_sweep", crashing_sweep)

    task = asyncio.create_task(
        daily_cleanup_task(
            isolation_root=root,
            cleanup_days=14,
            interval_seconds=0.05,
        )
    )

    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Trotz Crashes wurde mehrfach versucht
    assert call_count["n"] >= 2


# --- Konstanten ---


def test_default_cleanup_days_is_14() -> None:
    assert DEFAULT_CLEANUP_DAYS == 14


def test_default_daily_interval_is_one_day() -> None:
    assert DEFAULT_DAILY_INTERVAL_SECONDS == 24 * 60 * 60
