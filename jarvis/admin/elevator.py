"""Privilege-escalation seam (Wave 3, sub-task 3.4; AD-12 + AD-6 + AD-7).

The elevator is the *mechanism* that authorizes and spawns the privileged admin
helper bound to a transport address — the OS-driven auth prompt (a UAC dialog, a
polkit sheet, a Touch-ID/password prompt). It is deliberately separate from the
:class:`~jarvis.admin.transport.AdminTransport` byte seam: the elevator only
*starts* the helper; the helper still runs every op through the reused
``ipc._decode_request`` -> ``extra="forbid"`` schema -> argv builder ->
``shell=False`` chain. **The injection defenses are never weakened for
convenience** (AD-12 / the schema.py Safety mandate).

Implementations:

* :class:`UacElevator` (Windows) — wraps the existing dormant
  ``launcher.launch_elevated_helper`` ``ShellExecuteW("runas", ...)`` glue,
  unchanged (AD-7).
* :class:`PolkitElevator` (Linux, preferred) — ``pkexec`` spawns the helper.
* :class:`SudoElevator` (Linux, fallback) — ``sudo`` when polkit is absent.
* :class:`MacAuthElevator` (macOS) — ``osascript ... with administrator
  privileges`` spawns the helper; Touch-ID/password sheet is OS-driven.
* :class:`NullElevator` — the AD-6 graceful refusal. Returned on a headless box
  with no escalation mechanism. ``ensure_elevated_helper`` returns a refusal
  :class:`ElevationResult` and logs a clear English message; it **never
  raises** (AD-OE6 "zero silent drops") — the refusal is a typed result the
  caller surfaces as ``AdminResponse(success=False, ...)``.

``make_elevator()`` selects on ``detect_platform()`` + ``capabilities``:
``win32`` -> Uac; ``darwin`` -> MacAuth; ``linux`` -> Polkit if ``pkexec`` else
Sudo else Null; ``not has_elevation`` -> Null.

Import-cleanliness contract (HN-7): nothing platform-only is imported at module
scope. ``shutil``/``sys`` are stdlib; the ``win32*`` glue lives behind
:class:`UacElevator` -> ``launcher`` (lazy). ``import jarvis.admin.elevator``
succeeds on a Linux/macOS VPS.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from loguru import logger

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.platform import detect_platform
from jarvis.platform.capabilities import detect_capabilities


@dataclass(frozen=True, slots=True)
class ElevationResult:
    """Outcome of an :meth:`Elevator.ensure_elevated_helper` attempt.

    Frozen, mirroring the ``Event`` wire-type style. ``ok`` True means the
    privileged helper is (being) spawned and bound to ``transport_addr``;
    ``ok`` False is a graceful refusal/failure carrying a machine-readable
    ``error_code`` and a human English ``message`` — never an exception (AD-6).
    """

    ok: bool
    transport_addr: str | None = None
    pid: int | None = None
    error_code: str | None = None
    message: str | None = None


@runtime_checkable
class Elevator(Protocol):
    """Authorizes + spawns the privileged admin helper for this OS."""

    async def ensure_elevated_helper(self, transport_addr: str) -> ElevationResult:
        """Spawn/authorize the helper bound to ``transport_addr``.

        Returns an :class:`ElevationResult`; never raises (AD-6). The OS-driven
        auth prompt (UAC/polkit/Touch-ID) happens inside this call.
        """
        ...

    def is_available(self) -> bool:
        """True iff this elevation mechanism is usable on the current host."""
        ...


# ---------------------------------------------------------------------
# Shared helper-argv builder (argv only — shell=False, never a shell string)
# ---------------------------------------------------------------------


def _helper_argv(transport_addr: str) -> list[str]:
    """Validated argv for the helper: ``python -m jarvis.admin.helper ...``.

    argv list only (``shell=False``), exactly the executor's contract. The
    Windows path validates ``transport_addr`` against the pipe-name whitelist in
    ``launcher.launch_elevated_helper``; the POSIX path passes the socket path as
    a single argv element so no shell quoting/injection is possible.
    """
    return [sys.executable, "-m", "jarvis.admin.helper",
            "--pipe-name", transport_addr]


# ---------------------------------------------------------------------
# UacElevator (Windows) — the existing dormant runas glue (AD-7)
# ---------------------------------------------------------------------


class UacElevator:
    """Windows UAC elevation via the existing ``launcher.launch_elevated_helper``.

    No behavior change (AD-7): the ``ShellExecuteW("runas", ...)`` flow and its
    pipe-name whitelist live in ``launcher.py`` and are simply called here.
    """

    def is_available(self) -> bool:
        return detect_platform() == "win32"

    async def ensure_elevated_helper(self, transport_addr: str) -> ElevationResult:
        from .launcher import (
            HelperStartTimeoutError,
            UACCancelledError,
            launch_elevated_helper,
        )

        try:
            handle = await asyncio.to_thread(launch_elevated_helper, transport_addr)
        except UACCancelledError as exc:
            logger.warning("elevator.uac_declined", error=str(exc))
            return ElevationResult(
                ok=False, transport_addr=transport_addr,
                error_code="uac_declined",
                message="The UAC elevation prompt was declined; "
                        "privileged operation cannot proceed.",
            )
        except HelperStartTimeoutError as exc:
            logger.warning("elevator.uac_helper_timeout", error=str(exc))
            return ElevationResult(
                ok=False, transport_addr=transport_addr,
                error_code="helper_start_timeout",
                message="The elevated helper started but its pipe never became "
                        "ready in time.",
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning("elevator.uac_error", error=str(exc))
            return ElevationResult(
                ok=False, transport_addr=transport_addr,
                error_code="elevation_failed",
                message=f"Could not launch the elevated helper: {exc}",
            )
        return ElevationResult(
            ok=True, transport_addr=handle.pipe_name, pid=handle.pid,
        )


# ---------------------------------------------------------------------
# POSIX elevators — pkexec / sudo / osascript spawn the helper (shell=False)
# ---------------------------------------------------------------------


class _SubprocessElevator:
    """Shared spawn logic for the POSIX elevators (argv only, ``shell=False``)."""

    _wrapper: tuple[str, ...] = ()      # e.g. ("pkexec",) / ("sudo",)
    _tool_name: str = ""
    _error_code: str = "elevation_failed"

    def is_available(self) -> bool:
        return bool(self._tool_name and shutil.which(self._tool_name))

    def _build_argv(self, transport_addr: str) -> list[str]:
        return [*self._wrapper, *_helper_argv(transport_addr)]

    async def ensure_elevated_helper(self, transport_addr: str) -> ElevationResult:
        if not self.is_available():
            return ElevationResult(
                ok=False, transport_addr=transport_addr,
                error_code="elevation_unavailable",
                message=f"{self._tool_name!r} is not available on this host; "
                        "privileged operation cannot proceed.",
            )
        argv = self._build_argv(transport_addr)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except (OSError, ValueError) as exc:
            logger.warning("elevator.spawn_error",
                           tool=self._tool_name, error=str(exc))
            return ElevationResult(
                ok=False, transport_addr=transport_addr,
                error_code=self._error_code,
                message=f"Could not launch the elevated helper via "
                        f"{self._tool_name}: {exc}",
            )
        logger.info("elevator.helper_spawned",
                    tool=self._tool_name, pid=proc.pid)
        return ElevationResult(
            ok=True, transport_addr=transport_addr, pid=proc.pid,
        )


class PolkitElevator(_SubprocessElevator):
    """Linux polkit elevation: ``pkexec python -m jarvis.admin.helper ...``."""

    _wrapper = ("pkexec",)
    _tool_name = "pkexec"
    _error_code = "polkit_failed"


class SudoElevator(_SubprocessElevator):
    """Linux sudo fallback: ``sudo python -m jarvis.admin.helper ...``.

    ``sudo`` here relies on an existing askpass/cached-credential setup; with no
    TTY it fails fast and the result is surfaced as a typed refusal (never a
    hang or a crash). No ``shell=True``, argv only.
    """

    _wrapper = ("sudo",)
    _tool_name = "sudo"
    _error_code = "sudo_failed"


class MacAuthElevator:
    """macOS elevation via ``osascript ... with administrator privileges``.

    The Touch-ID/password sheet is OS-driven (like UAC). The helper command is
    assembled as a quoted ``do shell script`` string for osascript; the only
    interpolated value is the validated transport socket path. argv only at the
    ``osascript`` layer — ``shell=False``.
    """

    def is_available(self) -> bool:
        return detect_platform() == "darwin" and bool(shutil.which("osascript"))

    @staticmethod
    def _osascript_argv(transport_addr: str) -> list[str]:
        # quote each helper-argv element for the inner `do shell script` string.
        helper = " ".join(_quote_sh(a) for a in _helper_argv(transport_addr))
        script = f'do shell script {_quote_osa(helper)} with administrator privileges'
        return ["osascript", "-e", script]

    async def ensure_elevated_helper(self, transport_addr: str) -> ElevationResult:
        if not self.is_available():
            return ElevationResult(
                ok=False, transport_addr=transport_addr,
                error_code="elevation_unavailable",
                message="osascript is not available on this host; privileged "
                        "operation cannot proceed.",
            )
        argv = self._osascript_argv(transport_addr)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except (OSError, ValueError) as exc:
            logger.warning("elevator.macauth_error", error=str(exc))
            return ElevationResult(
                ok=False, transport_addr=transport_addr,
                error_code="macauth_failed",
                message=f"Could not launch the elevated helper via osascript: {exc}",
            )
        logger.info("elevator.helper_spawned", tool="osascript", pid=proc.pid)
        return ElevationResult(
            ok=True, transport_addr=transport_addr, pid=proc.pid,
        )


# ---------------------------------------------------------------------
# NullElevator — the AD-6 graceful refusal (never raises)
# ---------------------------------------------------------------------


_NULL_MESSAGE = (
    "no elevation mechanism available; privileged ops disabled — install pkexec "
    "or run with sudo (Linux), or run on a host with UAC/Authorization Services."
)


class NullElevator:
    """Headless / no-auth fallback. ``is_available`` is always False.

    ``ensure_elevated_helper`` returns a refusal :class:`ElevationResult` and
    logs the English message; it never raises (AD-6). The caller
    (``AdminClient``) surfaces this as ``AdminResponse(success=False,
    error_code="no_elevation")`` — a typed refusal, not a crash, not a silent
    drop (AD-OE6).
    """

    def is_available(self) -> bool:
        return False

    async def ensure_elevated_helper(self, transport_addr: str) -> ElevationResult:
        logger.info("elevator.null_refusal", reason="no_elevation")
        return ElevationResult(
            ok=False, transport_addr=transport_addr,
            error_code="no_elevation",
            message=_NULL_MESSAGE,
        )


# ---------------------------------------------------------------------
# Tiny shell/osascript quoting helpers (no shell is ever invoked; these only
# build the inner `do shell script` string for osascript safely)
# ---------------------------------------------------------------------


def _quote_sh(arg: str) -> str:
    """POSIX single-quote ``arg`` for embedding in a sh command string."""
    return "'" + arg.replace("'", "'\\''") + "'"


def _quote_osa(s: str) -> str:
    """Quote ``s`` as an AppleScript string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------


def make_elevator() -> Elevator:
    """Select the elevation mechanism for this host (AD-6 factory).

    * ``win32`` -> :class:`UacElevator`
    * ``darwin`` -> :class:`MacAuthElevator`
    * ``linux`` -> :class:`PolkitElevator` if ``pkexec`` else :class:`SudoElevator`
      else :class:`NullElevator`
    * ``not capabilities.has_elevation`` -> :class:`NullElevator`

    Never raises; the no-mechanism case returns :class:`NullElevator`, which
    refuses gracefully at call time.
    """
    if not detect_capabilities().has_elevation:
        return NullElevator()
    plat = detect_platform()
    if plat == "win32":
        return UacElevator()
    if plat == "darwin":
        elevator: Elevator = MacAuthElevator()
        return elevator if elevator.is_available() else NullElevator()
    # linux (and any other POSIX-shaped default)
    if shutil.which("pkexec"):
        return PolkitElevator()
    if shutil.which("sudo"):
        return SudoElevator()
    return NullElevator()


__all__ = [
    "Elevator",
    "ElevationResult",
    "UacElevator",
    "PolkitElevator",
    "SudoElevator",
    "MacAuthElevator",
    "NullElevator",
    "make_elevator",
]
