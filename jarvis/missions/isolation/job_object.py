"""Windows Job Object wrapper for worker subprocess reaping.

ADR-0009 §3 + Research-Doc §C: every worker subprocess is assigned to a
per-mission Windows Job Object with the limit flags
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK`.
When the orchestrator crashes (or closes the handle), the OS atomically
reaps the entire descendant tree of the mission — no zombies, no orphans.

Pattern: claude-squad/session/git/worktree.go + Microsoft win32-jobobject.

**Lazy imports:** pywin32 modules (`win32job`, `win32api`, `win32con`) are
imported only in the Win32 branch. On Linux/Mac the same API returns a
no-op object (`AlwaysOpenJobObject`) so tests and code paths work without
requiring pywin32.
"""
from __future__ import annotations

import logging
import sys
from types import TracebackType
from typing import Any

logger = logging.getLogger(__name__)


class _NoOpJobObject:
    """No-op implementation for non-Windows platforms.

    Exposes the same API as `WindowsJobObject` but does nothing, so
    Linux CI and unit tests can run without pywin32 or Windows.
    """

    def __init__(self, name: str | None = None) -> None:
        self._name = name
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def handle(self) -> Any:  # noqa: ANN401 — opaque on purpose
        return None

    async def __aenter__(self) -> "_NoOpJobObject":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    def assign(self, pid: int) -> None:
        # Non-Windows has no Job Objects — we only log; the Win32 test
        # verifies real semantics via psutil.
        logger.debug("NoOpJobObject.assign(pid=%d) — no-op (non-Windows)", pid)

    async def close(self) -> None:
        self._closed = True


class _Win32JobObjectImpl:
    """Real wrapper around pywin32 win32job calls.

    Limit flags:
    - `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: closing the last handle kills all
      assigned processes atomically, even on orchestrator crash.
    - `JOB_OBJECT_LIMIT_BREAKAWAY_OK`: allows children to detach from the job
      when spawned with `CREATE_BREAKAWAY_FROM_JOB`. This is needed so a
      worker subprocess (itself a job member) can still be explicitly assigned
      without inheritance (pattern from Research-Doc §C line 167).
    """

    def __init__(self, name: str | None = None) -> None:
        # Lazy import — Windows only.
        import win32job  # type: ignore[import-not-found]  # noqa: PLC0415

        self._win32job = win32job
        self._handle: Any = win32job.CreateJobObject(None, name or "")
        self._closed = False

        # Set limit flags.
        info = win32job.QueryInformationJobObject(
            self._handle, win32job.JobObjectExtendedLimitInformation
        )
        info["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | win32job.JOB_OBJECT_LIMIT_BREAKAWAY_OK
        )
        win32job.SetInformationJobObject(
            self._handle, win32job.JobObjectExtendedLimitInformation, info
        )

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def handle(self) -> Any:  # noqa: ANN401
        return self._handle

    async def __aenter__(self) -> "_Win32JobObjectImpl":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    def assign(self, pid: int) -> None:
        """Assign a running process (by PID) to the job object.

        Opens a handle with PROCESS_ALL_ACCESS, calls AssignProcessToJobObject,
        and closes the temporary handle. When the worker is spawned with
        `CREATE_BREAKAWAY_FROM_JOB` + `CREATE_NEW_PROCESS_GROUP`, assign() still
        places it in the job (Research-Doc §C lines 168-169).
        """
        if self._closed:
            raise RuntimeError("WindowsJobObject ist schon geschlossen")

        import win32api  # type: ignore[import-not-found]  # noqa: PLC0415
        import win32con  # type: ignore[import-not-found]  # noqa: PLC0415

        proc_handle = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, pid)
        try:
            self._win32job.AssignProcessToJobObject(self._handle, proc_handle)
        finally:
            win32api.CloseHandle(proc_handle)

    async def close(self) -> None:
        """Close the job handle — kills all assigned processes atomically.

        Sets ``_closed = True`` BEFORE attempting CloseHandle so that a failure
        does not leave us in a state where ``close()`` keeps re-trying (and
        keeps potentially leaking the handle on each retry). If CloseHandle
        raises we log it; the handle may still leak in that case but at least
        the second close-call is a no-op (FIX-6: _closed-Flag-Ordering).
        """
        if self._closed:
            return
        # Set flag FIRST to prevent double-close even if CloseHandle below fails.
        self._closed = True
        try:
            import win32api  # type: ignore[import-not-found]  # noqa: PLC0415

            win32api.CloseHandle(self._handle)
        except Exception:  # noqa: BLE001
            logger.warning(
                "WindowsJobObject.close() failed — handle may leak",
                exc_info=True,
            )


def WindowsJobObject(name: str | None = None) -> Any:  # noqa: ANN401, N802
    """Factory: returns a real Win32 wrapper on Windows, otherwise a no-op.

    A function (not a class) so the mandatory constructor resolves to the
    platform-appropriate implementation type. The API is identical:

        async with WindowsJobObject("mission-abc") as job:
            proc = subprocess.Popen([...], creationflags=CREATE_BREAKAWAY_FROM_JOB)
            job.assign(proc.pid)
            ...  # exit kills all assigned procs

    Sentinel via `sys.platform != 'win32'` — no `os.name` polling, because
    PyOxidizer/PyInstaller can set `os.name` to nt while pywin32 is absent.
    """
    if sys.platform == "win32":
        try:
            return _Win32JobObjectImpl(name)
        except ImportError:
            logger.warning(
                "pywin32 fehlt — WindowsJobObject faellt auf No-Op zurueck. "
                "Worker-Crash-Reaping ist NICHT garantiert."
            )
            return _NoOpJobObject(name)
    return _NoOpJobObject(name)


# Public alias for Linux tests that explicitly request the no-op.
AlwaysOpenJobObject = _NoOpJobObject
