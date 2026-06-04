"""Glide the real Windows cursor to a target, mirrored by the overlay.

This is the single seam the low-level mouse tools (``click``, ``click_element``,
``move_mouse``) use to position the OS cursor. Instead of teleporting with one
``SetCursorPos``, it animates an eased path so the user can *watch* the mouse
travel to where Computer-Use is about to act — and feeds every intermediate
point to the installed virtual-cursor overlay so the gold highlight tracks the
real pointer frame-for-frame.

Win32 access and ``sleep`` are injectable so the orchestration is unit-tested
off-Windows; on a headless VPS the whole thing degrades to a best-effort no-op
(the overlay singleton is already a no-op there, and a failed ``SetCursorPos``
is swallowed by the caller).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from jarvis.overlay.system_cursor import ping_jarvis_cursor
from jarvis.overlay.virtual_cursor import get_virtual_cursor, glide_cursor

logger = logging.getLogger(__name__)

# Default glide duration if neither caller nor config specifies one.
DEFAULT_GLIDE_MS: int = 220

# Process-wide glide duration, set once at desktop bootstrap from
# ``[computer_use].cursor_glide_ms``. Kept in a module global so a hot path
# (every click/move) never re-parses the TOML.
_glide_ms: int = DEFAULT_GLIDE_MS


def set_glide_ms(ms: int) -> None:
    """Set the default glide duration (ms). Called once at desktop bootstrap."""
    global _glide_ms
    _glide_ms = max(0, int(ms))


def _win_get_pos() -> tuple[int, int]:
    import ctypes
    from ctypes import wintypes

    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def _win_set_pos(x: int, y: int) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    if not user32.SetCursorPos(int(x), int(y)):
        raise ctypes.WinError(ctypes.get_last_error())


def _resolve_glide_ms() -> int:
    """Return the process-wide glide duration set at bootstrap."""
    return _glide_ms


def glide_os_cursor(
    x: int,
    y: int,
    *,
    duration_ms: int | None = None,
    get_pos: Callable[[], tuple[int, int]] = _win_get_pos,
    set_pos: Callable[[int, int], None] = _win_set_pos,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Glide the real OS cursor to ``(x, y)``, mirroring the overlay highlight.

    The final ``set_pos`` always lands exactly on the target so a click that
    follows never misses. The duration falls back to config, then to
    :data:`DEFAULT_GLIDE_MS`.
    """
    if duration_ms is None:
        duration_ms = _resolve_glide_ms()
    # Swap the OS arrow to the Jarvis cursor (or refresh the 30 s idle timer
    # if already active). Defence-in-depth alongside session_bracket — if the
    # mission's bracket failed to install, the cursor still swaps on the
    # first action.
    ping_jarvis_cursor()
    overlay = get_virtual_cursor()
    glide_cursor(
        int(x), int(y),
        get_pos=get_pos,
        set_pos=set_pos,
        duration_s=max(0.0, duration_ms / 1000.0),
        notify=overlay.show_path_point,
        sleep=sleep,
    )
