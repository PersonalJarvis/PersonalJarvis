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


def pin_thread_dpi_unaware() -> bool:
    """Pin the CALLING thread's DPI-awareness context to UNAWARE (Windows).

    Every top-level window the thread creates afterwards carries the UNAWARE
    context PER WINDOW: Windows bitmap-scales it to the monitor's scale factor
    (the familiar upscaled look on a 150 % display) and virtualises its
    coordinate space — and, critically, a later PROCESS-level awareness flip
    (pywebview's ``webview.start()`` calls ``user32.SetProcessDPIAware()`` at
    runtime, ``webview/platforms/winforms.py``) can no longer strip that
    scaling off the window. That flip is what made the JarvisBar shrink to
    ~2/3, jump position, and drag with a large cursor offset (recurring,
    boot-race dependent).

    The pin only survives such a flip when the process is ALREADY DPI-aware,
    so call :func:`ensure_dpi_awareness` first. The context is deliberately
    NOT restored — call this only from a thread fully owned by the unaware
    surface (e.g. the bar's dedicated Tk mainloop thread).

    Returns True when the pin took effect; False (graceful no-op) off Windows
    or when the API is unavailable (pre-Windows-10-1607).
    """
    if os.name != "nt":
        return False
    try:
        import ctypes  # noqa: PLC0415

        set_ctx = ctypes.windll.user32.SetThreadDpiAwarenessContext
        set_ctx.restype = ctypes.c_void_p
        set_ctx.argtypes = [ctypes.c_void_p]
        # DPI_AWARENESS_CONTEXT_UNAWARE == (DPI_AWARENESS_CONTEXT)-1
        prev = set_ctx(ctypes.c_void_p(-1))
        if prev is None:
            logger.debug("SetThreadDpiAwarenessContext(-1) returned NULL")
            return False
        return True
    except (OSError, AttributeError):
        logger.debug("SetThreadDpiAwarenessContext unavailable", exc_info=True)
        return False
