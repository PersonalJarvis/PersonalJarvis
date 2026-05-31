"""Win32 taskbar detection for the desktop-pet-style mascot anchor.

The mascot should sit on the top edge of the Windows taskbar, like a small
desktop pet standing on the taskbar line. To make that work robustly across
DPI scaling, multi-monitor setups, custom taskbar heights and auto-hidden
taskbars, we read the real taskbar rectangle from Win32 instead of guessing
pixel offsets from screen size.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskbarInfo:
    left: int
    top: int
    right: int
    bottom: int
    auto_hidden: bool

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def width(self) -> int:
        return self.right - self.left


def _get_window_rect(class_name: str, parent_hwnd: int = 0) -> Optional[Tuple[int, int, int, int]]:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        if parent_hwnd:
            hwnd = user32.FindWindowExW(parent_hwnd, 0, class_name, None)
        else:
            hwnd = user32.FindWindowW(class_name, None)
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except (OSError, AttributeError) as exc:
        logger.debug("Win32 lookup of %s failed: %s", class_name, exc)
        return None


def get_taskbar_rect() -> Optional[Tuple[int, int, int, int]]:
    return _get_window_rect("Shell_TrayWnd")


def get_tray_notify_rect() -> Optional[Tuple[int, int, int, int]]:
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        taskbar = user32.FindWindowW("Shell_TrayWnd", None)
        if not taskbar:
            return None
        return _get_window_rect("TrayNotifyWnd", parent_hwnd=int(taskbar))
    except (OSError, AttributeError) as exc:
        logger.debug("Win32 lookup of TrayNotifyWnd failed: %s", exc)
        return None


def is_taskbar_autohidden() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ABM_GETSTATE = 4
        ABS_AUTOHIDE = 1

        class _APPBARDATA(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint32),
                ("hWnd", ctypes.c_void_p),
                ("uCallbackMessage", ctypes.c_uint32),
                ("uEdge", ctypes.c_uint32),
                ("rc", ctypes.c_long * 4),
                ("lParam", ctypes.c_int64),
            ]

        abd = _APPBARDATA()
        abd.cbSize = ctypes.sizeof(_APPBARDATA)
        state = ctypes.windll.shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(abd))
        return bool(int(state) & ABS_AUTOHIDE)
    except (OSError, AttributeError) as exc:
        logger.debug("SHAppBarMessage failed: %s", exc)
        return False


def get_taskbar_info() -> Optional[TaskbarInfo]:
    rect = get_taskbar_rect()
    if rect is None:
        return None
    left, top, right, bottom = rect
    return TaskbarInfo(
        left=left, top=top, right=right, bottom=bottom,
        auto_hidden=is_taskbar_autohidden(),
    )


@dataclass(frozen=True)
class MascotAnchor:
    x: int
    y: int
    taskbar_aligned: bool


def compute_mascot_position(
    screen_w: int,
    screen_h: int,
    mascot_size: int,
    *,
    taskbar: Optional[TaskbarInfo] = None,
    tray_rect: Optional[Tuple[int, int, int, int]] = None,
    tray_safe_margin_px: int = 12,
    right_edge_margin_px: int = 24,
    overlap_px: int = 1,
    autohide_bottom_margin_px: int = 16,
) -> MascotAnchor:
    """Compute mascot top-left so it stands on the taskbar line."""
    _ = tray_rect, tray_safe_margin_px  # accepted for API compat

    taskbar_aligned = False
    if taskbar is not None and not taskbar.auto_hidden and taskbar.height > 4:
        y = taskbar.top - mascot_size + overlap_px
        taskbar_aligned = True
    else:
        y = screen_h - mascot_size - autohide_bottom_margin_px

    x = screen_w - mascot_size - right_edge_margin_px
    x = max(8, min(x, screen_w - mascot_size - 8))
    y = max(8, min(y, screen_h - mascot_size - 8))

    return MascotAnchor(x=x, y=y, taskbar_aligned=taskbar_aligned)


__all__ = [
    "MascotAnchor",
    "TaskbarInfo",
    "compute_mascot_position",
    "get_taskbar_info",
    "get_taskbar_rect",
    "get_tray_notify_rect",
    "is_taskbar_autohidden",
]
