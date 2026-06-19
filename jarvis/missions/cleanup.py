"""Cleanup logic for old mission directories.

ADR-0009 §"Open" worktree cleanup policy: retain for forensics, prune
after N days. Phase-5 MVP: 14 days default.

Two paths:
- ``startup_sweep`` — runs once on app start (no daemon needed).
- ``daily_cleanup_task`` — opt-in via ``[phase6.cleanup].daily=true``,
  runs as an asyncio.Task in the background with interval_seconds=86400.

Safety check: before deleting a directory we check its mtime against
cleanup_days. We NEVER remove anything whose mtime is below cleanup_days —
even if the directory name matches.

Empty-tree path: we first attempt ``git worktree remove --force``,
falling back to ``shutil.rmtree`` when the path is not registered as a worktree.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import Final

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

logger = logging.getLogger(__name__)


DEFAULT_CLEANUP_DAYS: Final[int] = 14
DEFAULT_DAILY_INTERVAL_SECONDS: Final[int] = 24 * 60 * 60


async def startup_sweep(
    *,
    isolation_root: Path,
    cleanup_days: int = DEFAULT_CLEANUP_DAYS,
    repo_root: Path | None = None,
) -> dict[str, int]:
    """Scans `isolation_root` and removes entries older than `cleanup_days`.

    Args:
        isolation_root: ``sub-agents-outputs/`` (mission-root container).
        cleanup_days: mtime cutoff in days.
        repo_root: optional cwd for ``git worktree remove``. If None,
            ``shutil.rmtree`` is used directly.

    Returns:
        Stats dict ``{"scanned": N, "removed": M, "errors": E}``.

    The actual filesystem/git work is fully blocking (``subprocess.run`` +
    ``shutil.rmtree`` over deep worktrees), so it runs in a worker thread via
    ``asyncio.to_thread``. This sweep executes on the same event loop that
    serves ``/api/health`` at boot — blocking it kept the desktop window
    from appearing for the entire sweep duration (the 30s-launch bug,
    2026-06-10).
    """
    return await asyncio.to_thread(
        _sweep_blocking,
        isolation_root=isolation_root,
        cleanup_days=cleanup_days,
        repo_root=repo_root,
    )


def _sweep_blocking(
    *,
    isolation_root: Path,
    cleanup_days: int,
    repo_root: Path | None,
) -> dict[str, int]:
    """Synchronous sweep body — only ever called off-loop (see above)."""
    stats = {"scanned": 0, "removed": 0, "errors": 0}

    if not isolation_root.exists():
        logger.info("startup_sweep: %s existiert nicht — skip", isolation_root)
        return stats

    cutoff_seconds = cleanup_days * 24 * 60 * 60
    now = time.time()

    for entry in isolation_root.iterdir():
        stats["scanned"] += 1
        try:
            mtime = entry.stat().st_mtime
        except OSError as exc:
            logger.warning("startup_sweep: stat failed for %s: %s", entry, exc)
            stats["errors"] += 1
            continue

        age_seconds = now - mtime
        if age_seconds < cutoff_seconds:
            continue  # too young — keep

        # Older than cutoff -> remove.
        if not _remove_entry(entry, repo_root=repo_root):
            stats["errors"] += 1
            continue
        stats["removed"] += 1

    if stats["scanned"] > 0:
        logger.info(
            "startup_sweep: scanned=%d removed=%d errors=%d",
            stats["scanned"], stats["removed"], stats["errors"],
        )
    return stats


def _on_rmtree_error(func, path, exc_info) -> None:  # type: ignore[no-untyped-def]
    """``shutil.rmtree`` onerror hook: clear the read-only bit and retry.

    Git marks object/pack files read-only; on Windows ``os.unlink`` fails on
    them with PermissionError, so without this hook stale mission worktrees
    were NEVER deletable — the backlog grew forever and every boot re-failed
    on the same entries. ``path`` always lies inside the tree being removed
    (rmtree does not follow symlinks/junctions), so the chmod cannot touch
    anything outside the mission directory.
    """
    exc = exc_info[1]
    if isinstance(exc, PermissionError):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
            return
        except OSError:
            pass
    raise exc


def _remove_entry(entry: Path, *, repo_root: Path | None) -> bool:
    """Attempts ``git worktree remove --force`` (when repo_root is given),
    falling back to ``shutil.rmtree``. Returns True on success.
    """
    # Heuristic: mission directories contain sub-tasks that are git worktrees.
    # We remove the `tasks/<NN>__*/workspace/` worktrees first, then the
    # entire mission directory.
    tasks_dir = entry / "tasks"
    if tasks_dir.exists() and repo_root is not None:
        for task_dir in tasks_dir.iterdir():
            workspace = task_dir / "workspace"
            if workspace.exists():
                _try_git_worktree_remove(workspace, repo_root)

    try:
        shutil.rmtree(entry, onerror=_on_rmtree_error)
        return True
    except OSError as exc:
        logger.warning("_remove_entry: rmtree failed for %s: %s", entry, exc)
        # Last resort with ignore_errors so that a file lock doesn't block
        # the entire sweep.
        try:
            shutil.rmtree(entry, ignore_errors=True)
            return not entry.exists()
        except Exception:  # noqa: BLE001
            return False


def _try_git_worktree_remove(workspace: Path, repo_root: Path) -> None:
    """Best-effort ``git worktree remove --force <workspace>``."""
    try:
        subprocess.run(  # noqa: S603
            ["git", "worktree", "remove", "--force", str(workspace)],
            cwd=str(repo_root),
            check=False,
            capture_output=True,
            timeout=10.0,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("git worktree remove failed: %s", exc)


async def daily_cleanup_task(
    *,
    isolation_root: Path,
    cleanup_days: int = DEFAULT_CLEANUP_DAYS,
    repo_root: Path | None = None,
    interval_seconds: int = DEFAULT_DAILY_INTERVAL_SECONDS,
) -> None:
    """Infinite loop: runs a sweep every `interval_seconds`.

    Started as ``asyncio.create_task(daily_cleanup_task(...))``.
    On app shutdown the task should be cancelled — the CancelledError
    is propagated cleanly.
    """
    logger.info(
        "daily_cleanup_task: starting (interval=%ds, cleanup_days=%d)",
        interval_seconds, cleanup_days,
    )
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await startup_sweep(
                    isolation_root=isolation_root,
                    cleanup_days=cleanup_days,
                    repo_root=repo_root,
                )
            except Exception:  # noqa: BLE001
                logger.warning("daily_cleanup_task: sweep failed", exc_info=True)
    except asyncio.CancelledError:
        logger.info("daily_cleanup_task: cancelled — shutting down")
        raise


__all__ = [
    "DEFAULT_CLEANUP_DAYS",
    "DEFAULT_DAILY_INTERVAL_SECONDS",
    "daily_cleanup_task",
    "startup_sweep",
]
