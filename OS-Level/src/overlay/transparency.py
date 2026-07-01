"""ctypes wrappers for Win32 window flags.

Plan §12.1 / §12.4: transparent click-through layered windows with
``WDA_EXCLUDEFROMCAPTURE`` so screen sharing and OBS don't pick up the
overlay. Qt sets ``WindowTransparentForInput`` itself; the ctypes path
here is (a) defense-in-depth for affinity, (b) prepares for Phase 9.6
(mascot needs ``WS_EX_NOACTIVATE`` selectively).

On non-Windows hosts all functions are no-ops, so tests can run
headless on any platform.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# Win32 constants — Plan §12.1, §12.2.
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

# WDA_EXCLUDEFROMCAPTURE: Win10 2004+ / Win11 — Plan §12.4.
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def _is_windows() -> bool:
    return sys.platform == "win32"


def get_user32():  # pragma: no cover — platform branch
    """Lazy loader. Only call on Windows."""
    if not _is_windows():
        raise RuntimeError("user32 is Windows-only")
    return ctypes.windll.user32


def apply_click_through(hwnd: int) -> None:
    """Adds ``WS_EX_LAYERED | WS_EX_TRANSPARENT`` to an HWND.

    Idempotent: existing style bits are OR'd in, nothing is cleared.
    """
    if not _is_windows():
        return
    user32 = get_user32()
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

    h = wintypes.HWND(hwnd)
    current = user32.GetWindowLongW(h, GWL_EXSTYLE)
    desired = current | WS_EX_LAYERED | WS_EX_TRANSPARENT
    if desired != current:
        user32.SetWindowLongW(h, GWL_EXSTYLE, desired)


def apply_mascot_styles(hwnd: int) -> None:
    """Mascot window — Plan §12.2: layered + no-activate + tool window.

    Deliberately NO ``WS_EX_TRANSPARENT``, so clicks/drag come through.
    Phase 9.6 uses this.
    """
    if not _is_windows():
        return
    user32 = get_user32()
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

    h = wintypes.HWND(hwnd)
    current = user32.GetWindowLongW(h, GWL_EXSTYLE)
    desired = current | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    if desired != current:
        user32.SetWindowLongW(h, GWL_EXSTYLE, desired)


def exclude_from_capture(hwnd: int) -> bool:
    """``SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)``.

    Returns ``True`` on success, ``False`` if unsupported (old Win10 builds)
    or when called on non-Windows.
    """
    if not _is_windows():
        return False
    user32 = get_user32()
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    result = user32.SetWindowDisplayAffinity(
        wintypes.HWND(hwnd), wintypes.DWORD(WDA_EXCLUDEFROMCAPTURE)
    )
    return bool(result)


def reapply_capture_affinity(hwnd: int) -> bool:
    """Plan §18.1 — reapply WDA_EXCLUDEFROMCAPTURE.

    Identical to ``exclude_from_capture``, but exposed as a named entry
    point for window hooks (showEvent, screenChanged, screenAdded). DWM
    doesn't cache the affinity flag across re-composite boundaries; after
    DPI changes or a monitor hotplug the flag has been observed to get
    lost. Hence re-apply it idempotently.
    """
    return exclude_from_capture(hwnd)


def set_per_monitor_dpi_awareness() -> None:
    """``SetProcessDpiAwareness(2)`` — PROCESS_PER_MONITOR_DPI_AWARE.

    Must run BEFORE the QApplication is created (Plan §12.3). Swallows
    all errors, since the call returns ``E_ACCESSDENIED`` when the
    awareness level is already set.
    """
    if not _is_windows():
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (OSError, AttributeError):  # pragma: no cover — best effort
        pass
