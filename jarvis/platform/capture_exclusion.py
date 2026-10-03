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

    # A private WinDLL instance, not ctypes.windll.user32: mutating argtypes
    # on the shared object corrupts every other caller in the process,
    # including pywebview's winforms SetWindowPos calls (see BUG-log 136x/day
    # ArgumentError crash).
    user32 = ctypes.WinDLL("user32", use_last_error=True)
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
    land on the parent or the overlay still photographs. Tk creates that
    parent only when the toplevel is first mapped, and the overlays call this
    before their first show — so the exclusion is also re-applied on every
    ``<Map>`` of the toplevel (a re-created wrapper starts without it). Before
    that fix the mascot sat in every appshot of the window under it.

    Returns True when the outer window is excluded now or will be on its first
    map. Best-effort: a missing handle or a test double without ``winfo_id``
    returns False.
    """
    if root is None:
        return False
    if sys.platform == "darwin":
        return exclude_macos_app_windows()
    if sys.platform != "win32":
        return False
    scheduled = _reapply_on_map(root)
    return _exclude_outer_tk_window(root) or scheduled


def _exclude_outer_tk_window(root: Any) -> bool:
    try:
        inner = int(root.winfo_id())
    except Exception:  # noqa: BLE001 — FakeRoot / torn-down interpreter
        return False
    user32 = _user32()
    if user32 is None or not inner:
        return False
    try:
        outer = int(user32.GetParent(inner) or 0)
    except Exception:  # noqa: BLE001
        outer = 0
    if not outer:
        # Not mapped yet: there is no outer window to exclude. The inner child
        # cannot carry a display affinity, so the <Map> hook does the work.
        return False
    return exclude_hwnd_from_capture(outer)


_MAP_HOOK_ATTR = "_jarvis_capture_exclusion_on_map"


def _reapply_on_map(root: Any) -> bool:
    """Bind one ``<Map>`` hook that re-excludes the toplevel's outer window."""
    if getattr(root, _MAP_HOOK_ATTR, False):
        return True
    bind = getattr(root, "bind", None)
    if not callable(bind):
        return False

    def _on_map(event: Any) -> None:
        # The toplevel's bind tag also fires for every child widget's map.
        if getattr(event, "widget", root) is root:
            _exclude_outer_tk_window(root)

    try:
        bind("<Map>", _on_map, "+")
        setattr(root, _MAP_HOOK_ATTR, True)
    except Exception:  # noqa: BLE001 — overlay must degrade, never crash
        log.debug("capture exclusion: could not hook <Map>", exc_info=True)
        return False
    return True


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
