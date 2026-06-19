"""Shared subprocess helpers for mission workers."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from contextlib import suppress as contextlib_suppress
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def worker_creationflags() -> int:
    """Return Windows creation flags used for detached worker processes."""
    if sys.platform != "win32":
        return 0
    return (
        getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", _CREATE_BREAKAWAY_FROM_JOB)
    )


async def create_worker_subprocess(
    cmd: list[str], **kwargs: Any
) -> asyncio.subprocess.Process:
    """Spawn a worker subprocess with graceful breakaway-flag degradation.

    The worker uses ``CREATE_BREAKAWAY_FROM_JOB`` so the per-mission Job Object
    can take ownership of the process tree (kill-on-close, no zombies). But when
    the host process (pythonw.exe) is itself inside a job that forbids breakaway,
    Windows denies ``CreateProcess`` with ``PermissionError`` (WinError 5) — and
    every worker spawn dies instantly, killing the mission as ``task_error``
    (live mission 019ec602, 2026-06-14, after the native ``claude.exe`` install
    replaced the old ``node cli.js`` path).

    Breakaway is an OPTIMIZATION, not a requirement: without it the worker still
    runs, just inside the parent's job. So on a WinError-5 ``PermissionError`` we
    retry once with the breakaway bit cleared rather than failing the spawn. A
    ``FileNotFoundError`` (missing binary) is a different problem and propagates
    unchanged. The caller passes ``creationflags=worker_creationflags()`` via
    kwargs is NOT required — this helper sources the flags itself so the retry
    can mutate them.
    """
    flags = worker_creationflags()
    kwargs.pop("creationflags", None)  # this helper owns the flags
    if sys.platform != "win32":
        # POSIX: spawn the worker as its own session/process-group leader so the
        # per-mission job object can reap the whole tree via os.killpg on close
        # (the Windows equivalent is the Job Object + CREATE_BREAKAWAY_FROM_JOB).
        kwargs.setdefault("start_new_session", True)
    try:
        return await asyncio.create_subprocess_exec(
            *cmd, creationflags=flags, **kwargs
        )
    except PermissionError as exc:
        if sys.platform != "win32" or not (flags & _CREATE_BREAKAWAY_FROM_JOB):
            raise
        safe_flags = flags & ~_CREATE_BREAKAWAY_FROM_JOB
        logger.warning(
            "worker spawn denied with CREATE_BREAKAWAY_FROM_JOB (%s) — retrying "
            "without breakaway; the host process is in a job that forbids it, so "
            "per-mission Job-Object isolation is degraded for this worker.",
            exc,
        )
        return await asyncio.create_subprocess_exec(
            *cmd, creationflags=safe_flags, **kwargs
        )


async def drain_stderr(
    stream: asyncio.StreamReader | None,
    log_path: Path,
) -> None:
    """Drain stderr to disk without blocking worker stdout consumption."""
    if stream is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as f:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            f.write(chunk)
            f.flush()


__all__ = [
    "contextlib_suppress",
    "create_worker_subprocess",
    "drain_stderr",
    "worker_creationflags",
]
