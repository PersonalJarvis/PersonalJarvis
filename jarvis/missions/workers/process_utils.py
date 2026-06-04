"""Shared subprocess helpers for mission workers."""
from __future__ import annotations

import asyncio
import subprocess
import sys
from contextlib import suppress as contextlib_suppress
from pathlib import Path


def worker_creationflags() -> int:
    """Return Windows creation flags used for detached worker processes."""
    if sys.platform != "win32":
        return 0
    return (
        getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
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


__all__ = ["contextlib_suppress", "drain_stderr", "worker_creationflags"]
