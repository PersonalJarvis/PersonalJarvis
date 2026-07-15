"""Win32 window hardening for the indicator sidecar (ctypes, lazy).

Two jobs, both quiet no-ops on every non-Windows platform:

- ``harden_window(hwnd)`` — belt-and-suspenders click-through: Qt already
  sets ``WindowTransparentForInput``, but Windows silently drops layered
  styles on some style mutations (BUG-030 class), so the extended styles
  are (re)applied directly and must be reapplied after show/screen-change.
- ``exclude_from_capture(hwnd)`` — ``SetWindowDisplayAffinity`` with
  ``WDA_EXCLUDEFROMCAPTURE`` so the border never appears in screenshots,
  including Computer-Use's OWN perception frames (Windows 10 2004+).
  Set ``JARVIS_CU_INDICATOR_CAPTURABLE=1`` to skip this — used by the
  live-verification flow, which needs to SEE the border in a screenshot.
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
_WDA_EXCLUDEFROMCAPTURE = 0x00000011

CAPTURABLE_ENV = "JARVIS_CU_INDICATOR_CAPTURABLE"


def _user32():
    if os.name != "nt":
        return None
    import ctypes  # noqa: PLC0415

    return ctypes.windll.user32  # type: ignore[attr-defined]


def harden_window(hwnd: int) -> bool:
    """OR the click-through/no-activate extended styles onto ``hwnd``."""
    user32 = _user32()
    if user32 is None or not hwnd:
        return False
    try:
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        wanted = (
            style
            | _WS_EX_LAYERED
            | _WS_EX_TRANSPARENT
            | _WS_EX_NOACTIVATE
            | _WS_EX_TOOLWINDOW
        )
        if wanted != style:
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, wanted)
        return True
    except Exception:  # noqa: BLE001
        log.debug("harden_window failed for hwnd=%s", hwnd, exc_info=True)
        return False


def exclude_from_capture(hwnd: int) -> bool:
    """Hide ``hwnd`` from all screen capture (BitBlt/mss/OBS/CU frames)."""
    if os.environ.get(CAPTURABLE_ENV, "").strip() in {"1", "true", "yes"}:
        return False
    user32 = _user32()
    if user32 is None or not hwnd:
        return False
    try:
        return bool(user32.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE))
    except Exception:  # noqa: BLE001
        log.debug("exclude_from_capture failed for hwnd=%s", hwnd, exc_info=True)
        return False


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
    "harden_window",
]
