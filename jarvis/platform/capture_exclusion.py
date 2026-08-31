"""Keep overlay windows out of screenshots on every OS that can.

The on-screen bar, mascot, and transcription bubble are for the person at
the keyboard. They must not appear in a Screen Context / Computer-Use /
screenshot-tool capture, or the model reads the last spoken line off the
glass instead of the screen the user asked it to look at.

Windows: ``SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`` — the window
stays visible to the user and is omitted from BitBlt/mss/OBS.
macOS: ``NSWindowSharingNone`` on overlay process windows.
Linux / headless: a quiet no-op (no equivalent API); callers may still
blank the overlay around a grab via ``capture_guard``.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

log = logging.getLogger(__name__)

_WDA_EXCLUDEFROMCAPTURE = 0x00000011
# AppKit.NSWindowSharingNone — keep the numeric literal so a host without
# pyobjc still documents the contract.
_NS_WINDOW_SHARING_NONE = 0


def _configure_user32(user32: Any, ctypes: Any, wintypes: Any) -> None:
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL


def _user32() -> Any:
    if sys.platform != "win32":
        return None
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    _configure_user32(user32, ctypes, wintypes)
    return user32


def exclude_hwnd_from_capture(hwnd: int) -> bool:
    """Hide one native window from screen capture. No-op off Windows."""
    if sys.platform != "win32" or not hwnd:
        return False
    user32 = _user32()
    if user32 is None:
        return False
    try:
        return bool(user32.SetWindowDisplayAffinity(int(hwnd), _WDA_EXCLUDEFROMCAPTURE))
    except Exception:  # noqa: BLE001 — overlay must degrade, never crash
        log.debug("exclude_hwnd_from_capture failed for hwnd=%s", hwnd, exc_info=True)
        return False


def exclude_tk_window_from_capture(root: Any) -> bool:
    """Exclude a Tk toplevel. Targets the outer ``TkTopLevel`` HWND on Windows.

    Tk's ``winfo_id()`` is the inner ``TkChild``; capture affinity has to
    land on the parent or the overlay still photographs. Best-effort: a
    missing handle or a test double without ``winfo_id`` returns False.
    """
    if root is None:
        return False
    if sys.platform == "darwin":
        return exclude_macos_app_windows()
    if sys.platform != "win32":
        return False
    try:
        inner = int(root.winfo_id())
    except Exception:  # noqa: BLE001 — FakeRoot / torn-down interpreter
        return False
    user32 = _user32()
    if user32 is None or not inner:
        return False
    try:
        outer = int(user32.GetParent(inner) or inner)
    except Exception:  # noqa: BLE001
        outer = inner
    return exclude_hwnd_from_capture(outer)


def exclude_macos_app_windows() -> bool:
    """Mark every NSWindow of this process as not shareable (overlay hosts).

    Safe only in the overlay companion / bar process, whose windows are all
    chrome. Best-effort: missing AppKit is a quiet no-op.
    """
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import NSApp  # type: ignore[import-not-found]  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return False
    try:
        wins = list(NSApp.windows())
        ok = False
        for win in wins:
            try:
                win.setSharingType_(_NS_WINDOW_SHARING_NONE)
                ok = True
            except Exception:  # noqa: BLE001, S112 — one window must not abort the rest
                continue
        return ok
    except Exception:  # noqa: BLE001
        log.debug("exclude_macos_app_windows failed", exc_info=True)
        return False


__all__ = [
    "exclude_hwnd_from_capture",
    "exclude_macos_app_windows",
    "exclude_tk_window_from_capture",
]
