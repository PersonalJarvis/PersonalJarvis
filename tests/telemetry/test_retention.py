"""Tests for telemetry.retention.sweep_old_blobs + retention_task."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from jarvis.telemetry.retention import (
    DEFAULT_RETENTION_DAYS,
    DEFAULT_RETENTION_INTERVAL_SECONDS,
    retention_task,
    sweep_old_blobs,
)


def _make_blob(blobs_dir: Path, name: str, *, age_days: float, size: int = 16) -> Path:
    """Create a blob file with mtime set ``age_days`` into the past."""
    blobs_dir.mkdir(parents=True, exist_ok=True)
    path = blobs_dir / name
    path.write_bytes(b"x" * size)
    when = time.time() - (age_days * 86400)
    os.utime(path, (when, when))
    return path


# --- sweep_old_blobs ---


@pytest.mark.asyncio
async def test_sweep_nonexistent_dir_returns_zero(tmp_path: Path) -> None:
    """No flight-recorder dir yet (fresh install): clean zero return."""
    stats = await sweep_old_blobs(flight_recorder_dir=tmp_path / "nope")
    assert stats == {"scanned": 0, "removed": 0, "errors": 0, "bytes_freed": 0}


@pytest.mark.asyncio
async def test_sweep_empty_blobs_dir_no_action(tmp_path: Path) -> None:
    (tmp_path / "blobs").mkdir(parents=True)
    stats = await sweep_old_blobs(flight_recorder_dir=tmp_path)
    assert stats["scanned"] == 0
    assert stats["removed"] == 0


@pytest.mark.asyncio
async def test_sweep_keeps_recent_blobs(tmp_path: Path) -> None:
    """Blobs younger than retention_days are untouched."""
    blobs = tmp_path / "blobs"
    fresh = _make_blob(blobs, "fresh.png", age_days=2)

    stats = await sweep_old_blobs(flight_recorder_dir=tmp_path, retention_days=10)
    assert stats["scanned"] == 1
    assert stats["removed"] == 0
    assert fresh.exists()


@pytest.mark.asyncio
async def test_sweep_removes_old_blobs(tmp_path: Path) -> None:
    """Blobs older than retention_days are deleted, bytes accounted."""
    blobs = tmp_path / "blobs"
    old = _make_blob(blobs, "old.png", age_days=30, size=1024)

    stats = await sweep_old_blobs(flight_recorder_dir=tmp_path, retention_days=10)
    assert stats["scanned"] == 1
    assert stats["removed"] == 1
    assert stats["bytes_freed"] == 1024
    assert not old.exists()


@pytest.mark.asyncio
async def test_sweep_mixed_keeps_fresh_removes_old(tmp_path: Path) -> None:
    blobs = tmp_path / "blobs"
    fresh = _make_blob(blobs, "fresh.jpg", age_days=1)
    old = _make_blob(blobs, "old.jpg", age_days=15)

    stats = await sweep_old_blobs(flight_recorder_dir=tmp_path, retention_days=10)
    assert stats["scanned"] == 2
    assert stats["removed"] == 1
    assert fresh.exists()
    assert not old.exists()


@pytest.mark.asyncio
async def test_sweep_retention_zero_disables(tmp_path: Path) -> None:
    """retention_days=0 means OFF — old blobs are kept, never deleted."""
    blobs = tmp_path / "blobs"
    old = _make_blob(blobs, "ancient.png", age_days=365)

    stats = await sweep_old_blobs(flight_recorder_dir=tmp_path, retention_days=0)
    assert stats == {"scanned": 0, "removed": 0, "errors": 0, "bytes_freed": 0}
    assert old.exists()


@pytest.mark.asyncio
async def test_sweep_negative_retention_disables(tmp_path: Path) -> None:
    blobs = tmp_path / "blobs"
    old = _make_blob(blobs, "ancient.png", age_days=365)

    stats = await sweep_old_blobs(flight_recorder_dir=tmp_path, retention_days=-5)
    assert stats["removed"] == 0
    assert old.exists()


@pytest.mark.asyncio
async def test_sweep_ignores_subdirectories(tmp_path: Path) -> None:
    """A stray subdirectory in blobs/ is not counted and not removed."""
    blobs = tmp_path / "blobs"
    old = _make_blob(blobs, "old.png", age_days=30)
    subdir = blobs / "nested"
    subdir.mkdir()
    when = time.time() - (30 * 86400)
    os.utime(subdir, (when, when))

    stats = await sweep_old_blobs(flight_recorder_dir=tmp_path, retention_days=10)
    assert stats["scanned"] == 1  # only the file, not the subdir
    assert stats["removed"] == 1
    assert subdir.exists()
    assert not old.exists()


@pytest.mark.asyncio
async def test_sweep_boundary_just_under_cutoff_is_kept(tmp_path: Path) -> None:
    """A blob slightly younger than the cutoff is kept (strict ``<`` boundary)."""
    blobs = tmp_path / "blobs"
    almost = _make_blob(blobs, "almost.png", age_days=9.9)

    stats = await sweep_old_blobs(flight_recorder_dir=tmp_path, retention_days=10)
    assert stats["removed"] == 0
    assert almost.exists()


# --- retention_task ---


@pytest.mark.asyncio
async def test_task_runs_sweep_then_cancels(tmp_path: Path) -> None:
    """Periodic task deletes old blobs across iterations, cancels cleanly."""
    blobs = tmp_path / "blobs"
    _make_blob(blobs, "old.png", age_days=30)

    task = asyncio.create_task(
        retention_task(
            flight_recorder_dir=tmp_path,
            retention_days=10,
            interval_seconds=0.05,
        )
    )
    await asyncio.sleep(0.15)
    assert not (blobs / "old.png").exists()  # first iteration removed it

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_task_continues_on_sweep_error(tmp_path: Path, monkeypatch) -> None:
    """A failing sweep is logged, the task keeps looping."""
    call_count = {"n": 0}

    async def crashing_sweep(*args, **kwargs):
        call_count["n"] += 1
        raise RuntimeError("simulated")

    monkeypatch.setattr(
        "jarvis.telemetry.retention.sweep_old_blobs", crashing_sweep
    )

    task = asyncio.create_task(
        retention_task(
            flight_recorder_dir=tmp_path,
            retention_days=10,
            interval_seconds=0.05,
        )
    )
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert call_count["n"] >= 2


# --- constants ---


def test_default_retention_days_is_10() -> None:
    assert DEFAULT_RETENTION_DAYS == 10


def test_default_interval_is_six_hours() -> None:
    assert DEFAULT_RETENTION_INTERVAL_SECONDS == 6 * 60 * 60
