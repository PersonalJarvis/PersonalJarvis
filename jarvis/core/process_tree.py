"""Kill a spawned process together with everything it started.

Terminating a child terminates exactly that child. Everything it spawned in
turn keeps running, reparented and unreachable — and for a coding CLI that is
not a detail, it is the bulk of the tree: every MCP server the CLI starts is a
separate ``npx`` process pair that does NOT exit when the pipe to its client
breaks. It waits on a stdin that will never speak again, forever.

Measured on this machine after a day of Agentic-IDE use, with the app already
closed: 102 ``node``, 59 ``cmd`` and 89 ``conhost`` processes still resident,
all of them descendants of panes that had been shut long before. Each workspace
that is opened adds another set, so the machine's idle floor climbs with every
session until a reboot — which is what "it was immediately back at 100 %" is
made of.

So a spawn is CONTAINED instead: the child is put in a kill-on-close container
before it can fork, and closing the container reaps the whole descendant tree
at once.

* **Windows** — a Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``.
  Descendants inherit membership automatically, and the kernel does the reaping
  when the last handle closes — including when this process dies without ever
  running its own cleanup.
* **POSIX** — the child's process GROUP, signalled ``SIGTERM`` and then
  ``SIGKILL`` after a short grace. A PTY child is a session leader
  (``ptyprocess`` calls ``setsid``), so its group is exactly its tree.
* **Anywhere else** — a no-op that says so once, because containment is a
  safeguard and its absence must not stop a terminal from opening.

Deliberately SYNCHRONOUS, unlike the mission-scoped container in
``jarvis/missions/isolation/job_object.py``: this one is closed from a PTY
teardown that runs on the event loop, where an ``await`` grace period would
stall every other pane. The POSIX escalation therefore runs on a short-lived
timer thread rather than a sleep.

``allow_breakaway`` is left ON. A process created with
``CREATE_BREAKAWAY_FROM_JOB`` inside a job that forbids breakaway does not
detach — it FAILS TO START, and a toolchain that does this (some installers,
some launchers) would turn containment into "the agent will not open". Nothing
in a normal CLI's tree asks to break away, so permitting it costs nothing real
and removes a whole class of spawn failures.
"""
from __future__ import annotations

import ctypes
import os
import signal
import sys
import threading
from ctypes import wintypes
from typing import Protocol

from loguru import logger

#: How long a POSIX tree gets to exit on ``SIGTERM`` before it is killed.
#: Long enough for a CLI to flush its state, short enough that a user closing a
#: workspace never waits on it (the escalation runs on its own thread anyway).
GRACE_S = 0.5

# Win32 constants — stable kernel API, declared here so the base install needs
# no pywin32 (that package lives in the desktop extra; containment must work
# without it).
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100

# Resolved once, by name: process groups and SIGKILL exist on POSIX only, and
# looking them up as attributes is what lets this module be imported, type-
# checked and unit-tested on a Windows host without a platform branch around
# every call.
_getpgid = getattr(os, "getpgid", None)
_killpg = getattr(os, "killpg", None)
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


class ProcessTree(Protocol):
    """One spawned process and everything it goes on to start."""

    @property
    def supports_containment(self) -> bool:
        """Whether this live container can enforce tree termination."""
        ...

    def assign(self, pid: int) -> None:
        """Put a freshly spawned process under this container's control."""

    def close(self) -> None:
        """Reap the tree. Safe to call more than once."""


class _NoContainment:
    """What an unsupported host gets: honest nothing.

    Reported once per container rather than per call — a workspace opens a
    dozen panes and a warning per pane would be a wall of the same line.
    """

    __slots__ = ("_name",)

    @property
    def supports_containment(self) -> bool:
        return False

    def __init__(self, name: str) -> None:
        self._name = name
        logger.debug(
            "No process-tree containment on this host — {} may leave descendants "
            "behind when it exits",
            name,
        )

    def assign(self, pid: int) -> None:
        return None

    def close(self) -> None:
        return None


class _WindowsJob:
    """A Win32 Job Object that kills its members when the handle closes."""

    __slots__ = ("_kernel32", "_handle", "_closed", "_name")

    @property
    def supports_containment(self) -> bool:
        return not self._closed

    def __init__(self, name: str) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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

        # Unnamed: a NAMED job is shared machine-wide, so two Jarvis instances
        # would land their panes in one container and the first one to quit
        # would kill the other's agents.
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel32 = kernel32
        self._handle = handle
        self._closed = False
        self._name = name
        try:
            info = _JobObjectExtendedLimitInformation()
            info.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_BREAKAWAY_OK
            )
            if not kernel32.SetInformationJobObject(
                handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            kernel32.CloseHandle(handle)
            self._closed = True
            raise

    def assign(self, pid: int) -> None:
        if self._closed or not pid:
            return
        process = self._kernel32.OpenProcess(
            _PROCESS_TERMINATE | _PROCESS_SET_QUOTA, False, pid
        )
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel32.CloseHandle(process)

    def close(self) -> None:
        if self._closed:
            return
        # Flagged before the call, so a failing CloseHandle cannot turn into a
        # retry loop that leaks a handle per attempt.
        self._closed = True
        if not self._kernel32.CloseHandle(self._handle):
            logger.debug(
                "Closing the job object for {} failed — its tree may survive: {}",
                self._name,
                ctypes.WinError(ctypes.get_last_error()),
            )


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = (
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    )


class _IoCounters(ctypes.Structure):
    _fields_ = (
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    )


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = (
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


class _PosixProcessGroup:
    """Reap a PTY child's process group: ``SIGTERM``, grace, ``SIGKILL``.

    The grace runs on a timer thread rather than an ``await``, because this is
    closed from a teardown on the event loop and half a second of blocking
    there is half a second in which no other pane draws.
    """

    __slots__ = ("_pgids", "_closed", "_name")

    @property
    def supports_containment(self) -> bool:
        return not self._closed

    def __init__(self, name: str) -> None:
        self._pgids: set[int] = set()
        self._closed = False
        self._name = name

    def assign(self, pid: int) -> None:
        if self._closed or not pid:
            return
        try:
            pgid = _getpgid(pid) if _getpgid is not None else pid
        except (ProcessLookupError, OSError):
            # Gone already, or no group to read — a PTY child is its own group
            # leader (``setsid``), so its pid IS the group in every case that
            # still matters.
            pgid = pid
        self._pgids.add(pgid)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        groups = tuple(self._pgids)
        self._pgids.clear()
        if not groups:
            return
        self._signal(groups, signal.SIGTERM)
        timer = threading.Timer(GRACE_S, self._signal, args=(groups, _SIGKILL))
        timer.daemon = True
        timer.start()

    def _signal(self, groups: tuple[int, ...], sig: int) -> None:
        if _killpg is None:
            return
        for pgid in groups:
            try:
                _killpg(pgid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                # Already gone, or not ours to signal. Reaping is best-effort by
                # nature: the tree exiting on its own is the good outcome.
                logger.debug(
                    "Signalling process group {} of {} did nothing — already gone",
                    pgid,
                    self._name,
                )


def make_process_tree(name: str) -> ProcessTree:
    """A kill-on-close container for one spawned process tree.

    Never raises. A host where containment cannot be set up gets the honest
    no-op: a terminal that opens and leaves debris behind is worth far more
    than one that refuses to open.

    ``name`` appears in the log lines only; it is never a Windows object name
    (see :class:`_WindowsJob`).
    """
    if sys.platform == "win32":
        try:
            return _WindowsJob(name)
        except Exception as exc:  # noqa: BLE001 - containment is a safeguard
            logger.warning(
                "Could not create a job object for {} — its descendants will "
                "not be reaped: {}",
                name,
                exc,
            )
            return _NoContainment(name)
    if _killpg is not None:
        return _PosixProcessGroup(name)
    return _NoContainment(name)


__all__ = ["GRACE_S", "ProcessTree", "make_process_tree"]
