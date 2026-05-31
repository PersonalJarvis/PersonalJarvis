"""Canonical folder + retention for development/verification screenshots.

Problem this solves: agents (and the maintainer's own snipping tool) drop UI
verification captures — ``chat_redesign.png``, ``Screenshot 2026-05-30 ....png``
— straight into the **repo root** (the process cwd), because no target folder is
conventional. They are already git-ignored (``/*.png`` in ``.gitignore``) but
nobody removes them, so the working tree slowly fills with stale frames.

This module gives those captures one explicit home — ``<repo>/screenshots/`` —
and a self-healing boot sweep:

1. :func:`consolidate_stray_root_screenshots` moves any stray root-level image
   into ``screenshots/`` (so wherever a capture lands, the next sweep tidies it).
2. :func:`prune_old_screenshots` deletes captures older than ``max_age_days``
   (by mtime). ``max_age_days <= 0`` disables the prune — ``0`` means "off",
   never "delete everything" (same convention as
   ``jarvis/telemetry/retention.py``).

Scope is deliberately the **development capture folder only**. App-runtime Vision
frames go to ``data/flight_recorder/blobs/`` and are handled by
``jarvis/telemetry/retention.py`` — this module never touches them.

Everything here is plain ``pathlib``/``shutil`` (cross-platform, no GPU, no
Windows API) and :func:`sweep_screenshots` never raises, so it is safe to call
unconditionally on app boot — including on a headless Linux VPS.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Final

from jarvis.core.paths import repo_root

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS: Final[int] = 10

# Suffixes we treat as screenshot captures (lower-cased comparison).
_IMAGE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
)


def _zero_stats() -> dict[str, int]:
    return {"scanned": 0, "removed": 0, "errors": 0, "bytes_freed": 0}


def screenshots_dir() -> Path:
    """Canonical directory for development/verification screenshots.

    Repo-relative (``<repo>/screenshots/``) on purpose: it is visible in the
    project folder the maintainer already works in, and it is the natural
    cwd-relative target for agent-driven captures. Does NOT create the
    directory — callers that need it on disk use :func:`sweep_screenshots`.
    """
    return repo_root() / "screenshots"


def _unique_destination(dest_dir: Path, name: str) -> Path:
    """Return a path in ``dest_dir`` for ``name`` that does not yet exist.

    On a name clash we append `` (1)``, `` (2)``, … to the stem rather than
    overwrite — the pre-existing capture is never clobbered.
    """
    candidate = dest_dir / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    i = 1
    while True:
        candidate = dest_dir / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def consolidate_stray_root_screenshots(root: Path, dest: Path) -> int:
    """Move stray root-level image files into ``dest``. Returns the move count.

    Only *direct* children of ``root`` are considered (never recursive), so the
    ``dest`` folder — and anything already inside it — is left untouched. Files
    whose suffix is not an image suffix are ignored. Never raises on a single
    bad entry; a per-file error is logged and skipped.
    """
    if not root.exists():
        return 0
    try:
        dest_resolved = dest.resolve()
    except OSError:
        dest_resolved = dest

    moved = 0
    for entry in root.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        # Defensive: never move a file that already lives in dest.
        try:
            if entry.resolve().parent == dest_resolved:
                continue
        except OSError:
            pass

        dest.mkdir(parents=True, exist_ok=True)
        target = _unique_destination(dest, entry.name)
        try:
            shutil.move(str(entry), str(target))
        except OSError as exc:
            logger.warning("consolidate: move failed for %s: %s", entry, exc)
            continue
        moved += 1

    if moved:
        logger.info("consolidate: moved %d stray screenshot(s) into %s", moved, dest)
    return moved


def prune_old_screenshots(
    directory: Path,
    *,
    max_age_days: int = DEFAULT_RETENTION_DAYS,
    now: float | None = None,
) -> dict[str, int]:
    """Delete files in ``directory`` older than ``max_age_days`` (by mtime).

    Args:
        directory: folder to sweep (flat scan — direct children only).
        max_age_days: age cutoff in days. ``<= 0`` disables the prune entirely.
        now: reference timestamp (defaults to ``time.time()``); injectable for
            tests.

    Returns:
        Stats dict ``{"scanned", "removed", "errors", "bytes_freed"}``.
    """
    if max_age_days <= 0:
        logger.debug("prune_old_screenshots: retention disabled (days=%d)", max_age_days)
        return _zero_stats()
    if not directory.exists():
        return _zero_stats()

    cutoff_seconds = max_age_days * 24 * 60 * 60
    ref = time.time() if now is None else now
    stats = _zero_stats()

    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        stats["scanned"] += 1
        try:
            st = entry.stat()
        except OSError as exc:
            logger.warning("prune_old_screenshots: stat failed for %s: %s", entry, exc)
            stats["errors"] += 1
            continue
        if ref - st.st_mtime < cutoff_seconds:
            continue  # too young — keep
        size = st.st_size
        try:
            entry.unlink()
        except OSError as exc:
            logger.warning("prune_old_screenshots: unlink failed for %s: %s", entry, exc)
            stats["errors"] += 1
            continue
        stats["removed"] += 1
        stats["bytes_freed"] += size

    if stats["removed"]:
        logger.info(
            "prune_old_screenshots: removed %d capture(s) older than %dd (%.1f MB freed)",
            stats["removed"],
            max_age_days,
            stats["bytes_freed"] / (1024 * 1024),
        )
    return stats


def sweep_screenshots(
    *,
    root: Path | None = None,
    dest: Path | None = None,
    max_age_days: int = DEFAULT_RETENTION_DAYS,
) -> dict[str, int]:
    """Boot-time entry: consolidate stray root captures, then prune old ones.

    Never raises — any unexpected failure is logged and swallowed, so wiring
    this into the app boot path can never break startup. Returns combined stats
    (``moved`` plus the prune stats).
    """
    root = repo_root() if root is None else root
    dest = screenshots_dir() if dest is None else dest

    stats: dict[str, int] = {"moved": 0, **_zero_stats()}
    try:
        dest.mkdir(parents=True, exist_ok=True)
        stats["moved"] = consolidate_stray_root_screenshots(root, dest)
        prune_stats = prune_old_screenshots(dest, max_age_days=max_age_days)
        for key in ("scanned", "removed", "errors", "bytes_freed"):
            stats[key] = prune_stats[key]
    except Exception:  # noqa: BLE001 — boot path must never raise
        logger.warning("sweep_screenshots: failed", exc_info=True)
    return stats


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "screenshots_dir",
    "consolidate_stray_root_screenshots",
    "prune_old_screenshots",
    "sweep_screenshots",
]
