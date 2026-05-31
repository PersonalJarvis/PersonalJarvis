"""File-based exclusive lock for WikiCurator runs.

Uses ``open(path, "x")`` semantics (``O_CREAT | O_EXCL``) for atomic
creation, which works on every OS without OS-specific primitives such as
``fcntl`` (Unix-only) or ``msvcrt.locking`` (Windows-only).

The lock file contains the writing process's PID and a monotonic
timestamp so a stale lock — one left behind by a crashed process — can
be detected and stolen automatically.

    with VaultLock(Path("data/wiki_curator.lock"), stale_after_seconds=300):
        ...  # only one process enters here at a time
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Field separator inside the lock file.  Must not appear in PID or timestamp.
_SEP = ";"


class VaultLock:
    """File-based exclusive lock for curator runs.

    Uses ``open(path, "x")`` semantics so creation is atomic on every
    OS.  Writes the current PID + a monotonic timestamp into the lock
    file so a stale lock (older than ``stale_after_seconds``) can be
    detected and stolen on next ``acquire``.

    Always usable as a context manager::

        with lock:
            ...
    """

    def __init__(self, path: Path, *, stale_after_seconds: int = 300) -> None:
        self._path = Path(path)
        self._stale_after = stale_after_seconds
        self._held = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self, *, timeout_s: float = 5.0) -> bool:
        """Block up to *timeout_s* waiting for the lock.

        Returns ``True`` when the lock is acquired, ``False`` when the
        timeout expires without acquiring.  A stale lock (PID gone or
        timestamp older than ``stale_after_seconds``) is stolen
        automatically and a WARNING is logged.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            if self._try_acquire():
                return True
            if time.monotonic() >= deadline:
                return False
            # Yield the CPU briefly before retrying — no busy-wait.
            time.sleep(0.05)

    def release(self) -> None:
        """Release the lock by removing the lock file.

        Safe to call multiple times; the second call is a no-op.
        """
        if not self._held:
            return
        self._held = False
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("VaultLock: could not remove lock file %s: %s", self._path, exc)

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> "VaultLock":
        if not self.acquire():
            raise TimeoutError(
                f"VaultLock: timed out waiting for lock at {self._path}"
            )
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _try_acquire(self) -> bool:
        """Single attempt to create the lock file atomically.

        Returns True on success, False when the lock is held by another
        process and is not yet stale.  Steals a stale lock and returns
        True in that case.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # ``open(path, "x")`` raises FileExistsError when the file
            # already exists — identical to O_CREAT|O_EXCL semantics.
            with open(self._path, "x") as fh:
                fh.write(f"{os.getpid()}{_SEP}{time.monotonic()}")
            self._held = True
            return True
        except FileExistsError:
            pass  # fall through to stale-detection

        # Lock file exists — read it and decide whether it is stale.
        if self._steal_if_stale():
            return True

        return False

    def _steal_if_stale(self) -> bool:
        """Read the existing lock file and steal it when stale.

        Returns True (and sets ``_held``) when the lock was stolen,
        False when it is fresh and still owned by a live process.
        """
        try:
            content = self._path.read_text(encoding="utf-8")
        except OSError:
            # File vanished between exists-check and read — another
            # process just released it.  The caller will retry.
            return False

        owner_pid, owner_ts = self._parse_lock_content(content)

        if owner_ts is not None:
            age = time.monotonic() - owner_ts
            if age <= self._stale_after:
                # Lock is fresh — do not steal.
                return False
            log.warning(
                "VaultLock: stealing stale lock (age=%.1fs, stale_after=%ds, "
                "owner_pid=%s) at %s",
                age,
                self._stale_after,
                owner_pid if owner_pid is not None else "?",
                self._path,
            )
        else:
            # Unparseable lock file — treat as stale.
            log.warning(
                "VaultLock: lock file %s is unreadable/corrupt — stealing it",
                self._path,
            )

        # Remove the stale file and try to create a fresh one.
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("VaultLock: could not remove stale lock %s: %s", self._path, exc)
            return False

        try:
            with open(self._path, "x") as fh:
                fh.write(f"{os.getpid()}{_SEP}{time.monotonic()}")
            self._held = True
            return True
        except FileExistsError:
            # Another process grabbed it between our unlink and create.
            return False

    @staticmethod
    def _parse_lock_content(content: str) -> tuple[int | None, float | None]:
        """Parse ``"<pid>;<monotonic_ts>"`` from lock file content.

        Returns ``(pid, timestamp)``; either value may be ``None`` when
        the file is corrupt.
        """
        parts = content.strip().split(_SEP, maxsplit=1)
        if len(parts) != 2:
            return None, None
        try:
            pid = int(parts[0])
            ts = float(parts[1])
            return pid, ts
        except ValueError:
            return None, None


__all__ = ["VaultLock"]
