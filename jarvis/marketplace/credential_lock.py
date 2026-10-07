"""Coordinate credential transactions and refreshes across loops and processes.

Lock files contain no credentials and live outside instance-specific data roots:
desktop and development instances use the same OS credential namespace.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

_STORAGE_LOCK = threading.RLock()
_STORAGE_LOCAL = threading.local()
_REFRESH_GUARD = threading.Lock()
_REFRESH_LOCKS: dict[str, threading.Lock] = {}


def _lock_path(name: str, directory: Path | None = None) -> Path:
    if directory is None:
        from jarvis.core.paths import user_data_dir

        directory = user_data_dir() / "credential-locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory / name


@contextmanager
def storage_lock(*, shared: bool = False, directory: Path | None = None) -> Iterator[None]:
    """Serialize complete multi-entry transactions, including nested reads."""
    with _STORAGE_LOCK:
        if not shared or getattr(_STORAGE_LOCAL, "held", False):
            yield
            return
        from filelock import FileLock, Timeout

        lock = FileLock(_lock_path("plugin-storage.lock", directory))
        try:
            lock.acquire(timeout=5)
        except Timeout:
            raise RuntimeError("Plugin credential storage is busy") from None
        _STORAGE_LOCAL.held = True
        try:
            yield
        finally:
            _STORAGE_LOCAL.held = False
            lock.release()


@asynccontextmanager
async def refresh_lock(
    plugin_id: str,
    *,
    shared: bool = False,
    directory: Path | None = None,
    wait_seconds: float = 35,
) -> AsyncIterator[None]:
    """One refresh per credential, without blocking an event loop while waiting.

    Each caller owns its own file-lock object: sharing a reentrant FileLock between
    async tasks would incorrectly let a second task rotate the same grant.
    """
    with _REFRESH_GUARD:
        gate = _REFRESH_LOCKS.setdefault(plugin_id, threading.Lock())
    deadline = time.monotonic() + wait_seconds
    while not gate.acquire(blocking=False):
        if time.monotonic() >= deadline:
            raise TimeoutError("Plugin refresh is already in progress")
        await asyncio.sleep(random.uniform(0.025, 0.075))  # noqa: S311
    file_lock = None
    try:
        if shared:
            from filelock import FileLock, Timeout

            digest = hashlib.sha256(plugin_id.encode("utf-8")).hexdigest()
            file_lock = FileLock(_lock_path(f"plugin-refresh-{digest}.lock", directory))
            while True:
                try:
                    file_lock.acquire(timeout=0)
                    break
                except Timeout:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Plugin refresh is already in progress") from None
                    await asyncio.sleep(random.uniform(0.025, 0.075))  # noqa: S311
        yield
    finally:
        if file_lock is not None and file_lock.is_locked:
            file_lock.release()
        gate.release()
