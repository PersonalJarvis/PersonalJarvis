"""Windows Job Object wrapper for worker subprocess reaping.

ADR-0009 §3 + Research-Doc §C: every worker subprocess is assigned to a
per-mission Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
Long-running mission-worker jobs allow explicit child breakaway; strict local
command jobs disable breakaway so their descendants cannot detach.
When the orchestrator crashes (or closes the handle), the OS atomically
reaps the entire descendant tree of the mission — no zombies, no orphans.

Pattern: claude-squad/session/git/worktree.go + Microsoft win32-jobobject.

**Lazy imports:** pywin32 modules (`win32job`, `win32api`, `win32con`) are
imported only in the Win32 branch, so tests and code paths run without
requiring pywin32. On Linux/macOS the factory returns
`_PosixProcessGroupJobObject`, which reaps each worker's session/process group
with SIGTERM→SIGKILL on close (workers are spawned with `start_new_session=True`).
`AlwaysOpenJobObject` (`_NoOpJobObject`) remains as an explicit no-op for tests.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import signal
import sys
from ctypes import wintypes
from types import TracebackType
from typing import Any

logger = logging.getLogger(__name__)

# SIGTERM exists on every platform; SIGKILL is POSIX-only, so fall back to its
# conventional number on Windows (the POSIX impl that uses it is never
# instantiated there, but the constants are referenced from cross-platform tests).
_SIGTERM = getattr(signal, "SIGTERM", 15)
_SIGKILL = getattr(signal, "SIGKILL", 9)


class _NoOpJobObject:
    """Explicit no-op used by tests and last-resort Windows degradation.

    Normal POSIX runs use ``_PosixProcessGroupJobObject``. API-worker local
    commands detect this no-op through ``handle is None`` and fail closed before
    starting a Windows target.
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

    async def __aenter__(self) -> _NoOpJobObject:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    def assign(self, pid: int) -> None:
        # This explicit test/degradation implementation deliberately records
        # no ownership. Security-sensitive callers must reject ``handle=None``.
        logger.debug("NoOpJobObject.assign(pid=%d) — no-op", pid)

    async def close(self) -> None:
        self._closed = True


class _Win32JobObjectImpl:
    """Real wrapper around pywin32 win32job calls.

    Limit flags:
    - `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: closing the last handle kills all
      assigned processes atomically, even on orchestrator crash.
    - `JOB_OBJECT_LIMIT_BREAKAWAY_OK` is optional. Mission-worker jobs enable it
      for explicit nested ownership; strict local-command jobs disable it so a
      descendant cannot request detachment.
    """

    def __init__(
        self, name: str | None = None, *, allow_breakaway: bool = True
    ) -> None:
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
        )
        if allow_breakaway:
            info["BasicLimitInformation"]["LimitFlags"] |= (
                win32job.JOB_OBJECT_LIMIT_BREAKAWAY_OK
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

    async def __aenter__(self) -> _Win32JobObjectImpl:
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
            raise RuntimeError("WindowsJobObject is already closed")

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


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Win32CtypesJobObjectImpl:
    """Dependency-free Win32 Job Object fallback for base installations.

    ``pywin32`` belongs to the desktop extra, but mission process containment
    must remain available in a base Windows install. These are stable Kernel32
    APIs, called lazily only on Windows. The API mirrors the pywin32-backed
    implementation exactly.
    """

    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100

    def __init__(
        self, name: str | None = None, *, allow_breakaway: bool = True
    ) -> None:
        if sys.platform != "win32":
            raise OSError("Win32 Job Objects are unavailable on this platform")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32 = kernel32
        self._closed = False

        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, name or None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        try:
            info = _JobObjectExtendedLimitInformation()
            info.BasicLimitInformation.LimitFlags = (
                self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            if allow_breakaway:
                info.BasicLimitInformation.LimitFlags |= (
                    self._JOB_OBJECT_LIMIT_BREAKAWAY_OK
                )
            ok = kernel32.SetInformationJobObject(
                handle,
                self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            kernel32.CloseHandle(handle)
            self._closed = True
            raise

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def handle(self) -> Any:  # noqa: ANN401
        return self._handle

    async def __aenter__(self) -> _Win32CtypesJobObjectImpl:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    def assign(self, pid: int) -> None:
        if self._closed:
            raise RuntimeError("WindowsJobObject is already closed")
        process = self._kernel32.OpenProcess(
            self._PROCESS_TERMINATE | self._PROCESS_SET_QUOTA,
            False,
            pid,
        )
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel32.CloseHandle(process)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._kernel32.CloseHandle(self._handle):
            logger.warning(
                "WindowsJobObject.close() failed — handle may leak: %s",
                ctypes.WinError(ctypes.get_last_error()),
            )


class _PosixProcessGroupJobObject:
    """POSIX kill-on-close containment via session/process-group signalling.

    The Windows Job Object reaps a worker's whole descendant tree atomically when
    its handle closes (``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``). POSIX has no direct
    userspace equivalent, but a worker spawned with ``start_new_session=True`` is
    its own process-group leader (``pgid == pid``), so signalling that group reaps
    the worker AND every grandchild it spawned (MCP servers, shell commands codex
    runs). ``assign()`` records each worker's group; ``close()`` sends ``SIGTERM``,
    waits a short grace, then ``SIGKILL`` to every recorded group — mirroring the
    Job Object for graceful shutdown, mission cancel/cleanup, worker timeout, and
    any handled orchestrator exception, all of which used to leak the tree.

    Honest limitation: a *hard* ``SIGKILL`` of the orchestrator process itself
    bypasses ``close()``, so it cannot reap there — the groups would be reparented
    to init and survive (the one case the kernel-level Job Object covers and this
    class alone does not). On Linux this is now covered separately: every worker
    is spawned via ``create_worker_subprocess``
    (``jarvis/missions/workers/process_utils.py``), which arms
    ``PR_SET_PDEATHSIG`` in a ``preexec_fn`` so the kernel itself SIGKILLs the
    worker leader the instant its parent dies, independent of this class's
    ``close()`` path. macOS still has no equivalent (a future launchd/kqueue
    ``EVFILT_PROC`` watcher would be the path there).
    """

    def __init__(
        self,
        name: str | None = None,
        *,
        getpgid: Any = None,  # noqa: ANN401 — injectable os.getpgid for tests
        killpg: Any = None,  # noqa: ANN401 — injectable os.killpg for tests
        grace_s: float = 0.5,
    ) -> None:
        self._name = name
        self._closed = False
        self._pgids: set[int] = set()
        # Keep injected overrides as-is; the POSIX-only os.getpgid/os.killpg are
        # resolved lazily at call time (assign/close), so merely CONSTRUCTING this
        # object on a Windows test host (the factory tests) never dereferences them.
        self._getpgid = getpgid
        self._killpg = killpg
        self._grace_s = grace_s

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def handle(self) -> Any:  # noqa: ANN401 — opaque; no OS handle on POSIX
        return None

    async def __aenter__(self) -> _PosixProcessGroupJobObject:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    def assign(self, pid: int) -> None:
        """Record a worker's process group so ``close()`` can reap the tree."""
        if self._closed:
            raise RuntimeError("PosixProcessGroupJobObject is already closed")
        getpgid = self._getpgid or os.getpgid
        try:
            pgid = getpgid(pid)
        except (ProcessLookupError, OSError):
            # Process already gone, or pgid unavailable — fall back to the pid as
            # its own group (true when spawned with start_new_session=True).
            pgid = pid
        self._pgids.add(pgid)

    def release(self, pid: int) -> None:
        """Forget a fully reaped short-lived group before its id can be reused.

        Long-running CLI workers remain registered until mission close. The
        in-process API worker, however, starts a fresh process group for every
        local command and reaps it immediately. Retaining that dead numeric pgid
        for the rest of a long mission could later signal an unrelated process
        group after kernel id reuse.
        """
        self._pgids.discard(pid)

    async def close(self) -> None:
        """SIGTERM, brief grace, then SIGKILL every recorded process group."""
        if self._closed:
            return
        self._closed = True
        self._signal_all(_SIGTERM)
        if self._pgids:
            await asyncio.sleep(self._grace_s)
        self._signal_all(_SIGKILL)

    def _signal_all(self, sig: int) -> None:
        killpg = self._killpg or os.killpg
        for pgid in self._pgids:
            try:
                killpg(pgid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                # Group already exited / not signalable — reaping is best-effort.
                logger.debug(
                    "killpg(%d, %d) — group already gone or not signalable", pgid, sig
                )


def WindowsJobObject(  # noqa: ANN401, N802
    name: str | None = None, *, allow_breakaway: bool = True
) -> Any:
    """Return kernel-backed Win32 containment or POSIX process-group reaping.

    A function (not a class) so the mandatory constructor resolves to the
    platform-appropriate implementation type. The API is identical:

        async with WindowsJobObject("mission-abc") as job:
            proc = subprocess.Popen([...], creationflags=CREATE_BREAKAWAY_FROM_JOB)
            job.assign(proc.pid)
            ...  # exit kills all assigned procs

    ``allow_breakaway=False`` creates the stricter command-job form. Sentinel
    via `sys.platform != 'win32'` — no `os.name` polling, because
    PyOxidizer/PyInstaller can set `os.name` to nt while pywin32 is absent.
    """
    if sys.platform == "win32":
        try:
            return _Win32JobObjectImpl(name, allow_breakaway=allow_breakaway)
        except Exception:  # noqa: BLE001 - fall through to dependency-free kernel API
            try:
                logger.info(
                    "pywin32 Job Object setup is unavailable; using the Kernel32 "
                    "fallback",
                    exc_info=True,
                )
                return _Win32CtypesJobObjectImpl(
                    name, allow_breakaway=allow_breakaway
                )
            except (OSError, AttributeError):
                logger.exception(
                    "Windows Job Object setup failed; process containment is unavailable"
                )
                return _NoOpJobObject(name)
    # macOS / Linux: real session/process-group reaping (SIGTERM→SIGKILL on close).
    return _PosixProcessGroupJobObject(name)


# Public alias for Linux tests that explicitly request the no-op.
AlwaysOpenJobObject = _NoOpJobObject
