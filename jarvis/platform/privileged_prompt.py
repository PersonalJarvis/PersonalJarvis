"""Runtime probe: is an OS privilege/elevation prompt blocking the screen NOW?

Unlike ``jarvis/platform/probes.py`` (static, cached capability flags computed
once per process), this answers a TRANSIENT question that Computer-Use asks
repeatedly mid-mission: *right now*, is a Windows UAC Secure-Desktop consent
prompt (or a macOS/Linux auth dialog) on screen that a non-elevated process can
neither screenshot nor click?

Why it matters: when an app raises a UAC prompt, Windows hoists the Secure
Desktop (``winsta0\\Winlogon``). A standard-integrity process literally cannot
capture it (BitBlt returns black or raises) and cannot send input to it (UIPI).
Computer-Use used to mistake this for a generic "couldn't see the screen" GDI
failure and abort. This probe lets the loop NAME the situation and pause for the
one unavoidable human click instead of aborting blind.

Defensive by contract (AD-6): any failure returns ``False`` — a false negative
just means "behave as before", a false positive would wrongly nag the user for a
UAC click that isn't there, so we bias hard toward ``False``. Lazy platform
imports only (HN-7): nothing platform-specific is imported at module scope.
Headless / no-display → ``False`` (the €5-VPS runtime has no UAC).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from . import detect_platform, probes

log = logging.getLogger(__name__)

#: Win32 ``GetLastError`` value returned when a standard-integrity process tries
#: to open the Winlogon input desktop the Secure Desktop owns during a UAC
#: prompt — itself a positive signal that the Secure Desktop is up.
_ERROR_ACCESS_DENIED = 5
#: ``GetUserObjectInformationW`` index for the object's name string.
_UOI_NAME = 2
#: The interactive desktop's name in normal use; anything else (notably
#: ``"Winlogon"``) means a secure/privileged desktop currently owns input.
_DEFAULT_DESKTOP_NAME = "default"


def privileged_prompt_active(probe: Callable[[], bool | None] | None = None) -> bool:
    """True iff a privileged OS prompt is blocking the interactive desktop now.

    ``probe`` overrides the per-platform probe (test seam / injection point).
    Returns ``False`` on any platform without a reliable runtime probe, on a
    headless host, on an "unknown" probe result, and on any error — a ``False``
    here only means "keep behaving as before", never a crash.
    """
    try:
        if not probes.display_present():
            return False
        used = probe or _platform_probe()
        if used is None:
            return False
        result = used()
        return result is True
    except Exception:  # noqa: BLE001 — a probe must never crash the loop
        log.debug("privileged_prompt_active probe failed", exc_info=True)
        return False


def _platform_probe() -> Callable[[], bool | None] | None:
    """Pick the per-OS runtime probe, or ``None`` where none is reliable yet."""
    plat = detect_platform()
    if plat == "win32":
        return _windows_secure_desktop_active
    # macOS ``SecurityAgent`` and Linux polkit prompts have no dependency-free,
    # reliable runtime probe today — the blank-frame heuristic at the capture
    # site is their cross-platform safety net. Returning None keeps this a
    # graceful no-op there (AD-6) rather than a guess.
    return None


def _windows_secure_desktop_active() -> bool | None:
    """True iff the Windows Secure Desktop (UAC consent) currently owns input.

    Two positive signals, either of which means "a UAC prompt is up":

    * the active input desktop's name is not ``"Default"`` (it is ``"Winlogon"``
      while the Secure Desktop is shown), or
    * ``OpenInputDesktop`` fails with ``ERROR_ACCESS_DENIED`` — a standard
      process cannot open the Winlogon input desktop the Secure Desktop owns, so
      that specific denial is itself the signal.

    Returns ``None`` on any unrelated failure (caller treats ``None`` as "don't
    know" → not active). Never raises.
    """
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    # HDESK OpenInputDesktop(DWORD dwFlags, BOOL fInherit, ACCESS_MASK access)
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.CloseDesktop.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    # BOOL GetUserObjectInformationW(HANDLE, int, PVOID, DWORD, LPDWORD)
    user32.GetUserObjectInformationW.restype = wintypes.BOOL
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]

    _DESKTOP_READOBJECTS = 0x0001
    hdesk = user32.OpenInputDesktop(0, False, _DESKTOP_READOBJECTS)
    if not hdesk:
        err = ctypes.get_last_error()
        if err == _ERROR_ACCESS_DENIED:
            # The input desktop is owned by a higher-privilege station — the
            # Secure Desktop is up.
            return True
        # Any other open failure is an unrelated condition we cannot interpret.
        return None

    try:
        buf = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD(0)
        ok = user32.GetUserObjectInformationW(
            hdesk,
            _UOI_NAME,
            buf,
            ctypes.sizeof(buf),
            ctypes.byref(needed),
        )
        if not ok:
            return None
        return buf.value.strip().lower() != _DEFAULT_DESKTOP_NAME
    finally:
        user32.CloseDesktop(hdesk)


__all__ = ["privileged_prompt_active"]
