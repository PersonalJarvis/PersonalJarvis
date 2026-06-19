"""Cross-platform mouse-cursor position backend (AI Pointer; AD-6 seam).

Follows the uniform platform seam: a ``CursorBackend`` ``Protocol``, a per-OS
implementation, a ``sys.platform`` factory, and a logged null-fallback that
returns ``None`` (never raises) when no cursor is readable (headless VPS,
Wayland, missing ``pynput``).

Import-cleanliness (HN-7): no platform-only package is imported at module scope.
``ctypes.windll`` (Windows) and ``pynput`` (macOS/Linux) are imported lazily
inside the method bodies, so ``import jarvis.platform.mouse`` stays clean on a
fresh ``python:3.11-slim`` Linux container.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from jarvis.platform import detect_platform
from jarvis.platform.capabilities import detect_capabilities

log = logging.getLogger(__name__)

# A screen point in physical pixels (DPI-aware coordinate space), or ``None``
# when the cursor position is not readable on this host.
CursorPos = tuple[int, int]


@runtime_checkable
class CursorBackend(Protocol):
    """The seam every per-OS cursor backend satisfies (AD-6)."""

    name: str

    def position(self) -> CursorPos | None:
        """Return the cursor ``(x, y)`` or ``None``. Must never raise."""
        ...


class WindowsCursorBackend:
    """Reads the cursor via ``user32.GetCursorPos`` (stdlib ``ctypes``)."""

    name = "windows-cursor"

    def position(self) -> CursorPos | None:
        try:
            import ctypes  # noqa: PLC0415 - lazy: windll exists only on Windows
            from ctypes import wintypes  # noqa: PLC0415

            pt = wintypes.POINT()
            ok = ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            if not ok:
                return None
            return int(pt.x), int(pt.y)
        except Exception:  # pragma: no cover - native call guard
            log.debug("GetCursorPos failed", exc_info=True)
            return None


class PynputCursorBackend:
    """Reads the cursor via ``pynput.mouse.Controller`` (macOS / Linux-X11)."""

    name = "pynput-cursor"

    def position(self) -> CursorPos | None:
        try:
            from pynput import mouse  # noqa: PLC0415 - lazy ([desktop] extra)

            x, y = mouse.Controller().position
            return int(x), int(y)
        except Exception:
            log.debug("pynput cursor position failed", exc_info=True)
            return None


class NullCursorBackend:
    """AD-6 graceful fallback: no cursor on this host (headless / Wayland)."""

    name = "null-cursor"
    _warned = False

    def position(self) -> CursorPos | None:
        if not NullCursorBackend._warned:
            NullCursorBackend._warned = True
            log.info(
                "AI Pointer: no readable mouse cursor on this host "
                "(headless, Wayland, or pynput missing); cursor context disabled."
            )
        return None


def make_cursor_backend() -> CursorBackend:
    """Select the cursor backend for this host (AD-6).

    * ``win32`` → :class:`WindowsCursorBackend` (stdlib ctypes).
    * else if ``capabilities.has_cursor`` → :class:`PynputCursorBackend`.
    * else → :class:`NullCursorBackend` (logged-once no-op).

    Never raises and never returns a platform backend whose dependency is
    absent — the factory itself is the graceful seam.
    """
    if detect_platform() == "win32":
        return WindowsCursorBackend()
    if detect_capabilities().has_cursor:
        return PynputCursorBackend()
    return NullCursorBackend()


__all__ = [
    "CursorBackend",
    "CursorPos",
    "WindowsCursorBackend",
    "PynputCursorBackend",
    "NullCursorBackend",
    "make_cursor_backend",
]
