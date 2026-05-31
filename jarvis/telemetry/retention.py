"""Retention policy for captured screenshot blobs (auto-delete after N days).

Jarvis captures screenshots into ``data/flight_recorder/blobs/`` for in-session
context — both the Vision system (``jarvis/vision/screenshot.py``, the dominant
writer in production) and the Flight-Recorder event blobs
(``jarvis/telemetry/recorder.py``) write there, content-addressed by sha256.
These frames are throwaway after the session; without a retention sweep the
directory grows without bound (observed in the field: ~91k files / ~38 GB).

This module deletes blob files older than ``retention_days`` (by mtime). It
mirrors ``jarvis/missions/cleanup.py``: a one-shot :func:`sweep_old_blobs` for
app boot plus a periodic :func:`retention_task` asyncio loop. The blocking
filesystem walk runs in a worker thread (``asyncio.to_thread``) so a directory
with tens of thousands of files never stalls the event loop.

Scope is deliberately the **screenshot blobs only**. The per-day ``*.jsonl``
event logs in the same directory are the low-level context/event stream — they
are consumed by the Sub-Agents board aggregator (``jarvis/board/aggregator.py``)
and belong to the session-context retention feature, so this sweep leaves them
untouched.

``retention_days <= 0`` disables the sweep (returns zero stats) — ``0`` means
"off", never "delete everything".
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)


DEFAULT_RETENTION_DAYS: Final[int] = 10
# Screenshot blobs accumulate fast, so we re-sweep more often than the daily
# mission cleanup — every 6 h keeps disk usage bounded during long-running
# sessions without being wasteful. The age cutoff is still ``retention_days``;
# this only controls how often we re-check.
DEFAULT_RETENTION_INTERVAL_SECONDS: Final[int] = 6 * 60 * 60

# Subdirectory (relative to the flight-recorder dir) that holds the screenshot
# blobs. MUST match the writers: ``jarvis/telemetry/recorder.py`` (``_blobs_dir``)
# and ``jarvis/vision/screenshot.py`` (``_DEFAULT_BLOB_DIR``).
_BLOBS_SUBDIR: Final[str] = "blobs"


def _zero_stats() -> dict[str, int]:
    return {"scanned": 0, "removed": 0, "errors": 0, "bytes_freed": 0}


def _sweep_blobs_sync(
    blobs_dir: Path, *, cutoff_seconds: float, now: float
) -> dict[str, int]:
    """Synchronous scan-and-delete — runs in a worker thread.

    Iterates the (flat) ``blobs/`` directory and unlinks every regular file
    whose mtime is older than ``cutoff_seconds``. Never raises on a single bad
    entry; per-file ``OSError`` increments the ``errors`` counter instead.
    """
    stats = _zero_stats()
    for entry in blobs_dir.iterdir():
        if not entry.is_file():
            continue  # blobs/ is flat; ignore stray subdirectories
        stats["scanned"] += 1
        try:
            st = entry.stat()
        except OSError as exc:
            logger.warning("sweep_old_blobs: stat failed for %s: %s", entry, exc)
            stats["errors"] += 1
            continue

        if now - st.st_mtime < cutoff_seconds:
            continue  # too young — keep

        size = st.st_size
        try:
            entry.unlink()
        except OSError as exc:
            logger.warning("sweep_old_blobs: unlink failed for %s: %s", entry, exc)
            stats["errors"] += 1
            continue
        stats["removed"] += 1
        stats["bytes_freed"] += size
    return stats


async def sweep_old_blobs(
    *,
    flight_recorder_dir: Path,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> dict[str, int]:
    """Delete screenshot blobs older than ``retention_days`` (by mtime).

    The blocking filesystem walk is offloaded to a worker thread so a directory
    with tens of thousands of blobs does not stall the event loop.

    Args:
        flight_recorder_dir: the ``data/flight_recorder`` directory; blobs live
            in its ``blobs/`` subdirectory.
        retention_days: age cutoff in days. ``<= 0`` disables the sweep.

    Returns:
        Stats dict ``{"scanned": N, "removed": M, "errors": E, "bytes_freed": B}``.
    """
    if retention_days <= 0:
        logger.debug("sweep_old_blobs: retention disabled (days=%d)", retention_days)
        return _zero_stats()

    blobs_dir = flight_recorder_dir / _BLOBS_SUBDIR
    if not blobs_dir.exists():
        logger.debug("sweep_old_blobs: %s does not exist — skip", blobs_dir)
        return _zero_stats()

    cutoff_seconds = retention_days * 24 * 60 * 60
    stats = await asyncio.to_thread(
        _sweep_blobs_sync, blobs_dir, cutoff_seconds=cutoff_seconds, now=time.time()
    )

    if stats["removed"] > 0:
        logger.info(
            "sweep_old_blobs: scanned=%d removed=%d errors=%d freed=%.1f MB",
            stats["scanned"],
            stats["removed"],
            stats["errors"],
            stats["bytes_freed"] / (1024 * 1024),
        )
    else:
        logger.debug(
            "sweep_old_blobs: scanned=%d, nothing older than %dd to remove",
            stats["scanned"],
            retention_days,
        )
    return stats


async def retention_task(
    *,
    flight_recorder_dir: Path,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    interval_seconds: int = DEFAULT_RETENTION_INTERVAL_SECONDS,
) -> None:
    """Infinite loop: re-runs :func:`sweep_old_blobs` every ``interval_seconds``.

    Started as ``asyncio.create_task(retention_task(...))`` and cancelled on app
    shutdown — the ``CancelledError`` is propagated cleanly.
    """
    logger.info(
        "retention_task: starting (interval=%ds, retention_days=%d)",
        interval_seconds,
        retention_days,
    )
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await sweep_old_blobs(
                    flight_recorder_dir=flight_recorder_dir,
                    retention_days=retention_days,
                )
            except Exception:  # noqa: BLE001
                logger.warning("retention_task: sweep failed", exc_info=True)
    except asyncio.CancelledError:
        logger.info("retention_task: cancelled — shutting down")
        raise


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "DEFAULT_RETENTION_INTERVAL_SECONDS",
    "retention_task",
    "sweep_old_blobs",
]
