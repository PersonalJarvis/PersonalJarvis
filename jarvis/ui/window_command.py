"""Minimize, maximize and close for the desktop window, from the page.

The page draws those buttons itself once the native title bar is gone
(``frameless``). The methods are the same ones the operating system would
call, so closing still runs the window's normal close path.

Headless hosts have no window. Callers get ``ok: false`` and do not pretend
a button was pressed.
"""

from __future__ import annotations

import sys
from typing import Any

_ACTIONS = frozenset({"minimize", "maximize", "close"})


def window_chrome(platform: str | None = None) -> dict[str, Any]:
    """Where the page should put the window buttons.

    macOS keeps them on the left, like the traffic lights they replace.
    Windows and Linux keep them on the right. ``frameless`` is filled in by
    the desktop shell from the live window — this helper only names the side.
    """
    plat = platform if platform is not None else sys.platform
    if plat == "darwin":
        name, controls = "darwin", "leading"
    elif plat == "win32":
        name, controls = "windows", "trailing"
    else:
        name, controls = "linux", "trailing"
    return {"ok": True, "frameless": False, "platform": name, "controls": controls}


def run_window_command(
    window: Any,
    action: str,
    *,
    maximized: bool | None = None,
) -> dict[str, Any]:
    """Run one caption action on ``window``.

    ``maximized`` is the operating system's answer when the caller has one
    (Windows ``IsZoomed``). Without it, the last action this process took is
    the answer — enough for macOS and Linux, where only these buttons change
    that state.
    """
    if window is None:
        return {"ok": False, "reason": "no_window"}
    if action not in _ACTIONS:
        return {"ok": False, "reason": "unknown_action"}
    if action == "minimize":
        window.minimize()
        window._jarvis_maximized = False
        return {"ok": True, "maximized": False}
    if action == "close":
        window.destroy()
        return {"ok": True, "maximized": False}
    is_max = (
        maximized
        if maximized is not None
        else bool(getattr(window, "_jarvis_maximized", False))
    )
    if is_max:
        window.restore()
        window._jarvis_maximized = False
        return {"ok": True, "maximized": False}
    window.maximize()
    window._jarvis_maximized = True
    return {"ok": True, "maximized": True}
