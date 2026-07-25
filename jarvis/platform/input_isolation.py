"""Can ordinary user software still type into our window?

Dictation apps (Wispr Flow, Windows Voice Access, superwhisper, Talon, ...),
text expanders, clipboard managers, and password-manager auto-type all work the
same way: they synthesize keystrokes or a clipboard paste into whichever window
currently has focus, and they locate the target field through the OS accessibility
tree. Both of those are privilege-gated.

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
    "windows_process_is_elevated",
]
