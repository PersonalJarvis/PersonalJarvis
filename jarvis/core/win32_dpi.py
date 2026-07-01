"""DPI awareness — set once, idempotent.

Pattern extracted from ``jarvis/vision/screenshot.py`` so that
``jarvis/awareness/watchers/window.py`` and ``jarvis/vision/screenshot.py``
can share the same helper — without a cross-module import between two
peer subpackages and without a duplicated idempotency flag. Whoever calls
first sets the awareness level; later callers see the module-global flag
and return as a no-op.

On Linux/Mac the entire function is a no-op — tests run
platform-independently.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Module-global idempotency flag. Not thread-safe, but DPI awareness is
# typically set in the main thread during bootstrap — and is itself idempotent
# at the Win32 level (E_ACCESSDENIED on a second set is OK).
_DPI_AWARENESS_SET: bool = False


def ensure_dpi_awareness() -> None:
    """Sets PER_MONITOR_AWARE_V2 via shcore (or user32 fallback).

    Idempotent — safe to call multiple times without side effects. Non-Windows
    is a no-op; under Win32 the first call sets the awareness level,
    all subsequent calls are no-ops.

    - ``shcore.SetProcessDpiAwareness(2)`` is the modern API (Windows 8.1+).
    - ``user32.SetProcessDPIAware()`` as fallback for older systems.
    """
    global _DPI_AWARENESS_SET
    if _DPI_AWARENESS_SET:
        return
    if os.name != "nt":
        _DPI_AWARENESS_SET = True
        return
    try:
        import ctypes  # noqa: PLC0415

        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        res = ctypes.windll.shcore.SetProcessDpiAwareness(2)
        # S_OK == 0; E_ACCESSDENIED is returned when already set — that is OK.
        if res not in (0, -2147024891):  # 0x80070005 = E_ACCESSDENIED
            logger.debug("SetProcessDpiAwareness returned 0x%x", res & 0xFFFFFFFF)
    except (OSError, AttributeError):
        try:
            import ctypes  # noqa: PLC0415

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            logger.warning("Could not set DPI awareness", exc_info=True)
    finally:
        _DPI_AWARENESS_SET = True
