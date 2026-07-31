"""Can ordinary user software still type into our window?

Dictation and voice-typing apps, OS accessibility voice control, text expanders,
clipboard managers, and password-manager auto-type all work the same way: they
synthesize keystrokes or a clipboard paste into whichever window currently has
focus, and they locate the target field through the OS accessibility tree. Both
of those are privilege-gated.

**Windows UIPI (User Interface Privilege Isolation).** A process may not send
synthetic input to — nor read the automation tree of — a window owned by a
process of *higher* integrity. So the moment the desktop app runs elevated
("Run as administrator"), every one of those tools goes silently dead inside our
window while continuing to work everywhere else. Silently is literal: Microsoft
documents that ``SendInput`` blocked by UIPI reports failure through neither its
return value nor ``GetLastError``, so the dictation app believes it succeeded and
the user sees a window that simply ignores their voice.

Measured on the maintainer's box (2026-07-25), an elevated Jarvis window exposed
**1** automation element and **0** text fields to a normal-privilege client,
against 418/80 for an ordinary window; the identical WebView, launched
unelevated, accepted both synthetic typing and a Ctrl+V paste.

Nothing about this is Jarvis-specific — which is why the fix belongs here rather
than in any one feature. The app is *designed* to run unelevated (the autostart
scheduled task pins ``RunLevel=Limited``, and privileged operations go through
the separate admin helper in ``jarvis/admin/``), but an app that was ever started
elevated stays elevated across every in-app restart, so the condition is sticky
and invisible without a probe.

POSIX hosts get the honest analogue: a GUI running as root is isolated from the
user session's assistive tooling in the same spirit, but there is no safe way for
us to drop back to the original user, so we report it without promising a repair.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from . import PlatformName, detect_platform

log = logging.getLogger(__name__)


class InputIsolationReason(StrEnum):
    """Why third-party input software cannot reach this window."""

    #: Nothing in the way.
    NONE = "none"
    #: Windows: we run elevated, so UIPI drops input from normal-privilege apps.
    ELEVATED = "elevated"
    #: POSIX: we run as root, outside the user's assistive-tech session.
    ROOT = "root"
    #: The privilege state could not be read. Never treated as a defect.
    UNKNOWN = "unknown"


_ELEVATED_SUMMARY = (
    "This app is running with administrator rights, so Windows blocks other "
    "programs from typing into its window. Dictation apps, text expanders, "
    "clipboard tools, and password-manager auto-type will appear to do nothing "
    "here while still working in every other app."
)
_ELEVATED_REMEDY = (
    "Restart the app without administrator rights. Nothing in normal operation "
    "needs them — privileged actions ask for elevation individually."
)
_ROOT_SUMMARY = (
    "This app is running as root, so dictation apps, text expanders, and other "
    "assistive input tools running in your normal user session cannot type into "
    "its window."
)
_ROOT_REMEDY = (
    "Start the app as your normal user account instead of with sudo/root."
)


@dataclass(frozen=True)
class InputIsolationReport:
    """What outside input software can and cannot do with our window."""

    blocked: bool
    reason: InputIsolationReason
    platform: PlatformName
    summary: str
    remedy: str
    can_restart_unelevated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "reason": str(self.reason),
            "platform": self.platform,
            "summary": self.summary,
            "remedy": self.remedy,
            "can_restart_unelevated": self.can_restart_unelevated,
        }


def windows_process_is_elevated() -> bool | None:
    """Is this process running with an elevated (administrator) token?

    ``None`` when the answer cannot be determined — including on every
    non-Windows host, where the question does not apply. Never raises: this is a
    diagnostic, and a diagnostic that can crash the app is worse than no
    diagnostic at all.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes  # noqa: PLC0415 — lazy: keeps this module import-clean (HN-7)
        from ctypes import wintypes  # noqa: PLC0415

        TOKEN_QUERY = 0x0008
        TokenElevation = 20

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        # Declaring these is load-bearing, not decoration: ctypes assumes a
        # 32-bit int return/argument for anything undeclared, which truncates
        # 64-bit HANDLEs. Without it OpenProcessToken receives a mangled
        # pseudo-handle, fails, and this probe reports "unknown" on every
        # Windows host — a broken measurement perfectly disguised as our own
        # fail-open behaviour (caught only by a live check, 2026-07-25).
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL

        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
        ):
            return None
        try:
            elevated = wintypes.DWORD()
            returned = wintypes.DWORD()
            ok = advapi32.GetTokenInformation(
                token,
                TokenElevation,
                ctypes.cast(ctypes.byref(elevated), wintypes.LPVOID),
                ctypes.sizeof(elevated),
                ctypes.byref(returned),
            )
            if not ok:
                return None
            return bool(elevated.value)
        finally:
            kernel32.CloseHandle(token)
    except Exception:  # noqa: BLE001 — an unreadable token is "unknown", not fatal
        log.debug("Could not read this process's elevation state", exc_info=True)
        return None


def windows_foreground_window_is_elevated() -> bool | None:
    """Is the window currently in the FOREGROUND owned by an elevated process?

    The mirror image of :func:`windows_process_is_elevated`: that one asks
    "can other software type into us", this one asks "can we type into what the
    user is looking at". Same UIPI rule, opposite direction — and the same
    silence: a ``SendInput`` blocked by UIPI reports failure through neither its
    return value nor ``GetLastError``, so dictation would paste into the void
    and report success. Measured on a live desktop 2026-07-02.

    ``None`` when the answer cannot be determined — including on every
    non-Windows host, where the question does not apply. Never raises: a
    diagnostic that can crash the caller is worse than no diagnostic.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes  # noqa: PLC0415 — lazy: keeps this module import-clean (HN-7)
        from ctypes import wintypes  # noqa: PLC0415

        TOKEN_QUERY = 0x0008
        TokenElevation = 20
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        # Declaring these is load-bearing, not decoration — see the sibling
        # function: an undeclared 64-bit HANDLE is silently truncated to int.
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None

        process = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if not process:
            # Access denied reading a higher-integrity process is itself a
            # strong hint that it IS elevated — but it is not proof (the
            # handle can fail for other reasons), and this probe must not
            # invent findings. "Unknown" is the honest answer.
            return None
        try:
            token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(
                process, TOKEN_QUERY, ctypes.byref(token)
            ):
                return None
            try:
                elevated = wintypes.DWORD()
                returned = wintypes.DWORD()
                ok = advapi32.GetTokenInformation(
                    token,
                    TokenElevation,
                    ctypes.cast(ctypes.byref(elevated), wintypes.LPVOID),
                    ctypes.sizeof(elevated),
                    ctypes.byref(returned),
                )
                if not ok:
                    return None
                return bool(elevated.value)
            finally:
                kernel32.CloseHandle(token)
        finally:
            kernel32.CloseHandle(process)
    except Exception:  # noqa: BLE001 — an unreadable token is "unknown", not fatal
        log.debug("Could not read the foreground window's elevation", exc_info=True)
        return None


def macos_secure_input_enabled() -> bool | None:
    """Is macOS Secure Input active right now?

    A password field turns it on (``EnableSecureEventInput``), and while it is
    on, keyboard events stop reaching every other process. Synthetic keystrokes
    are therefore unsafe: the paste chord may simply not arrive.

    ``None`` on non-macOS hosts or when the API cannot be reached. Never raises.
    """
    if sys.platform != "darwin":
        return None
    try:
        import ctypes  # noqa: PLC0415 — lazy (HN-7)
        import ctypes.util  # noqa: PLC0415

        path = ctypes.util.find_library("Carbon")
        if not path:
            return None
        carbon = ctypes.cdll.LoadLibrary(path)
        probe = getattr(carbon, "IsSecureEventInputEnabled", None)
        if probe is None:
            return None
        probe.argtypes = []
        probe.restype = ctypes.c_bool
        return bool(probe())
    except Exception:  # noqa: BLE001 — an unreadable state is "unknown", not fatal
        log.debug("Could not read the macOS Secure Input state", exc_info=True)
        return None


def _euid() -> int | None:
    """Effective user id, or ``None`` on platforms without one (Windows)."""
    getter = getattr(os, "geteuid", None)
    if getter is None:
        return None
    try:
        return int(getter())
    except OSError:  # pragma: no cover — geteuid does not fail in practice
        return None


def describe_input_isolation(
    *,
    _platform=detect_platform,
    _elevated=windows_process_is_elevated,
    _euid=_euid,
) -> InputIsolationReport:
    """Report whether outside input software can reach this app's window.

    Fail-open by design: when the privilege state cannot be read we report
    ``UNKNOWN`` and ``blocked=False``, because warning a user about a problem
    they may not have — and offering a restart they do not need — is worse than
    staying quiet. A real block is always a positive measurement.
    """
    platform = _platform()

    if platform == "win32":
        elevated = _elevated()
        if elevated is None:
            return InputIsolationReport(
                blocked=False,
                reason=InputIsolationReason.UNKNOWN,
                platform=platform,
                summary="",
                remedy="",
                can_restart_unelevated=False,
            )
        if elevated:
            return InputIsolationReport(
                blocked=True,
                reason=InputIsolationReason.ELEVATED,
                platform=platform,
                summary=_ELEVATED_SUMMARY,
                remedy=_ELEVATED_REMEDY,
                can_restart_unelevated=True,
            )
        return InputIsolationReport(
            blocked=False,
            reason=InputIsolationReason.NONE,
            platform=platform,
            summary="",
            remedy="",
            can_restart_unelevated=False,
        )

    if _euid() == 0:
        return InputIsolationReport(
            blocked=True,
            reason=InputIsolationReason.ROOT,
            platform=platform,
            summary=_ROOT_SUMMARY,
            remedy=_ROOT_REMEDY,
            # Dropping privileges to "the user who ran sudo" is guesswork and
            # would strand file ownership; the user restarts this one himself.
            can_restart_unelevated=False,
        )

    return InputIsolationReport(
        blocked=False,
        reason=InputIsolationReason.NONE,
        platform=platform,
        summary="",
        remedy="",
        can_restart_unelevated=False,
    )


__all__ = [
    "InputIsolationReason",
    "InputIsolationReport",
    "describe_input_isolation",
    "macos_secure_input_enabled",
    "windows_foreground_window_is_elevated",
    "windows_process_is_elevated",
]
