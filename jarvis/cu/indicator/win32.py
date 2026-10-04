"""Win32 window hardening for the indicator sidecar (ctypes, lazy).

Three jobs, all quiet no-ops on every non-Windows platform:

- ``harden_window(hwnd)`` — belt-and-suspenders click-through: Qt already
  sets ``WindowTransparentForInput``, but Windows silently drops layered
  styles on some style mutations (BUG-030 class), so the extended styles
  are (re)applied directly and must be reapplied after show/screen-change.
- ``exclude_from_capture(hwnd)`` — ``SetWindowDisplayAffinity`` with
  ``WDA_EXCLUDEFROMCAPTURE`` so the border never appears in screenshots,
  including Computer-Use's OWN perception frames (Windows 10 2004+).
  Set ``JARVIS_CU_INDICATOR_CAPTURABLE=1`` to skip this — used by the
  live-verification flow, which needs to SEE the border in a screenshot.
- ``hide_system_cursor()`` / ``restore_system_cursor()`` — while Jarvis
  operates the screen the agent pointer replaces the system pointer; the
  user's own cursor scheme always comes back (see the section below).
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080

CAPTURABLE_ENV = "JARVIS_CU_INDICATOR_CAPTURABLE"


def _configure_user32(user32, ctypes, wintypes) -> None:
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL


def _user32():
    if os.name != "nt":
        return None
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    # A private WinDLL instance, not ctypes.windll.user32: mutating argtypes
    # on the shared object corrupts every other caller in the process,
    # including pywebview's winforms SetWindowPos calls (see BUG-log 136x/day
    # ArgumentError crash).
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    _configure_user32(user32, ctypes, wintypes)
    return user32


def harden_window(hwnd: int) -> bool:
    """OR the click-through/no-activate extended styles onto ``hwnd``."""
    user32 = _user32()
    if user32 is None or not hwnd:
        return False
    try:
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        wanted = style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW
        if wanted != style:
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, wanted)
        return True
    except Exception:  # noqa: BLE001
        log.debug("harden_window failed for hwnd=%s", hwnd, exc_info=True)
        return False


def harden_clickable_window(hwnd: int) -> bool:
    """Layered, never-activating tool window that still takes clicks.

    For the appshot card: like :func:`harden_window` minus click-through, so
    its rounded corners are see-through and a click does not steal focus.
    Also asks Windows 11 for no frame border and no corner rounding of its
    own, which would otherwise draw a grey outline around the transparent
    margin. A quiet no-op elsewhere.
    """
    user32 = _user32()
    if user32 is None or not hwnd:
        return False
    try:
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        wanted = style | _WS_EX_LAYERED | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW
        wanted &= ~_WS_EX_TRANSPARENT
        if wanted != style:
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, wanted)
    except Exception:  # noqa: BLE001
        log.debug("harden_clickable_window failed for hwnd=%s", hwnd, exc_info=True)
        return False
    _no_dwm_frame(hwnd)
    return True


def _no_dwm_frame(hwnd: int) -> None:
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    try:
        dwmapi = ctypes.WinDLL("dwmapi")
        for attribute, value in ((33, 1), (34, 0xFFFFFFFE)):  # DONOTROUND, COLOR_NONE
            data = wintypes.DWORD(value)
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), attribute, ctypes.byref(data), ctypes.sizeof(data)
            )
    except Exception:  # noqa: BLE001 - older Windows: a thin frame stays
        log.debug("DWM frame attributes unavailable", exc_info=True)


def strip_window_frame(hwnd: int) -> bool:
    """Drop the grey Windows 11 frame border and corner rounding from ``hwnd``.

    Windows 11 outlines even frameless, transparent windows, which shows as a
    second grey line around an overlay. A quiet no-op elsewhere.
    """
    if _user32() is None or not hwnd:
        return False
    _no_dwm_frame(hwnd)
    return True


def exclude_from_capture(hwnd: int) -> bool:
    """Hide ``hwnd`` from all screen capture (BitBlt/mss/OBS/CU frames)."""
    if os.environ.get(CAPTURABLE_ENV, "").strip() in {"1", "true", "yes"}:
        return False
    from jarvis.platform.capture_exclusion import (  # noqa: PLC0415
        exclude_hwnd_from_capture,
    )

    return exclude_hwnd_from_capture(hwnd)


# ---------------------------------------------------------------------------
# System pointer swap: while Jarvis operates the screen, the agent pointer
# drawn by the sidecar IS the pointer. Windows lets one process replace the
# system-wide cursor images (SetSystemCursor) and reload the user's own
# scheme from the registry (SPI_SETCURSORS) — the restore works from ANY
# process, so the main app can heal a sidecar that died mid-control.
# ---------------------------------------------------------------------------

#: Every standard cursor slot an application can show (OCR_* in winuser.h).
_CURSOR_SLOTS = (
    32512, 32513, 32514, 32515, 32516, 32642, 32643, 32644,
    32645, 32646, 32648, 32649, 32650, 32651,
)  # fmt: skip
_SPI_SETCURSORS = 0x0057
_CURSOR_MARKER = "cu-pointer-hidden"


def _cursor_marker():
    from jarvis.core.paths import user_data_dir  # noqa: PLC0415

    return user_data_dir() / _CURSOR_MARKER


def _blank_cursor(user32, ctypes):
    """A fully transparent 32x32 cursor (AND mask all set, XOR mask clear)."""
    size = 32 * 32 // 8
    and_mask = (ctypes.c_ubyte * size)(*([0xFF] * size))
    xor_mask = (ctypes.c_ubyte * size)()
    return user32.CreateCursor(None, 0, 0, 32, 32, and_mask, xor_mask)


def hide_system_cursor() -> bool:
    """Swap every system cursor for a transparent one. ``False`` elsewhere.

    A marker file records the swap first, so :func:`restore_system_cursor`
    in any later process (the main app at boot, or after reaping a crashed
    sidecar) knows the user's pointer still needs to come back.
    """
    if os.name != "nt":
        return False
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    try:
        marker = _cursor_marker()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        # Without the marker a crash could leave the pointer invisible for
        # good. Keep the user's pointer instead of risking that.
        log.warning("cursor marker unwritable; keeping the system pointer", exc_info=True)
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateCursor.restype = wintypes.HANDLE
    user32.CreateCursor.argtypes = [
        wintypes.HINSTANCE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p,
    ]  # fmt: skip
    user32.SetSystemCursor.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    user32.SetSystemCursor.restype = wintypes.BOOL
    swapped = 0
    for slot in _CURSOR_SLOTS:
        # SetSystemCursor takes ownership of (and destroys) the handle, so
        # every slot gets its own blank cursor.
        handle = _blank_cursor(user32, ctypes)
        if handle and user32.SetSystemCursor(handle, slot):
            swapped += 1
    if not swapped:
        restore_system_cursor()
        return False
    return True


def restore_system_cursor(*, only_if_marked: bool = False) -> bool:
    """Reload the user's own cursor scheme. Safe to call any number of times.

    ``only_if_marked`` restores only when a swap is on record — the cheap
    boot/reap check that never touches the cursor in the common case.
    """
    if os.name != "nt":
        return False
    try:
        marker = _cursor_marker()
        marked = marker.exists()
    except OSError:
        marked = False
    if only_if_marked and not marked:
        return False
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SystemParametersInfoW.argtypes = [
        wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT,
    ]  # fmt: skip
    user32.SystemParametersInfoW.restype = wintypes.BOOL
    ok = bool(user32.SystemParametersInfoW(_SPI_SETCURSORS, 0, None, 0))
    if ok and marked:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            log.debug("cursor marker not removable", exc_info=True)
    if not ok:
        log.warning("restoring the system cursor scheme failed")
    return ok


def capture_exclusion_available() -> bool:
    """True where the OS can hide the border from screenshots (Windows).

    Platforms without this API need the blank/unblank capture guard
    around Computer-Use's own frame grabs instead.
    """
    return os.name == "nt"


__all__ = [
    "CAPTURABLE_ENV",
    "capture_exclusion_available",
    "exclude_from_capture",
    "harden_clickable_window",
    "harden_window",
    "hide_system_cursor",
    "restore_system_cursor",
]
