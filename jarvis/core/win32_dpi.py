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


# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (winuser.h). V2 additionally
# covers non-client areas and child windows and is the declaration
# window-centric Computer-Use relies on: window rects, monitor metrics and
# SendInput normalization all read the SAME physical-pixel virtual desktop.
_DPI_CTX_PER_MONITOR_V2 = -4
_E_ACCESSDENIED = -2147024891  # 0x80070005 — awareness already set: fine


def _apply_process_awareness(windll) -> str:
    """Declare process DPI awareness, most capable tier first.

    Returns which tier landed: ``"per_monitor_v2"`` | ``"per_monitor"`` |
    ``"system"`` | ``"none"``. Takes the ``ctypes.windll`` namespace injected
    so the ladder is testable on any OS. Never raises.

    1. ``user32.SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)``
       (Windows 10 1703+) — the declaration the multi-monitor window
       pipeline requires.
    2. ``shcore.SetProcessDpiAwareness(2)`` (Windows 8.1+, per-monitor V1).
    3. ``user32.SetProcessDPIAware()`` (system-aware last resort).
    """
    import ctypes  # noqa: PLC0415

    try:
        set_ctx = windll.user32.SetProcessDpiAwarenessContext
        if set_ctx(ctypes.c_void_p(_DPI_CTX_PER_MONITOR_V2)):
            return "per_monitor_v2"
    except (OSError, AttributeError):
        logger.debug("SetProcessDpiAwarenessContext unavailable", exc_info=True)
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2; E_ACCESSDENIED = already set.
        res = windll.shcore.SetProcessDpiAwareness(2)
        if res in (0, _E_ACCESSDENIED):
            return "per_monitor"
        logger.debug("SetProcessDpiAwareness returned 0x%x", res & 0xFFFFFFFF)
    except (OSError, AttributeError):
        logger.debug("SetProcessDpiAwareness unavailable", exc_info=True)
    try:
        if windll.user32.SetProcessDPIAware():
            return "system"
    except (OSError, AttributeError):
        logger.warning("Could not set DPI awareness", exc_info=True)
    return "none"


def ensure_dpi_awareness() -> None:
    """Declare the process PER_MONITOR_AWARE_V2 (with graceful fallbacks).

    Idempotent — safe to call multiple times without side effects. Non-Windows
    is a no-op; under Win32 the first call sets the awareness level,
    all subsequent calls are no-ops. See :func:`_apply_process_awareness`
    for the exact ladder (V2 context -> shcore per-monitor -> system-aware).
    """
    global _DPI_AWARENESS_SET
    if _DPI_AWARENESS_SET:
        return
    if os.name != "nt":
        _DPI_AWARENESS_SET = True
        return
    try:
        import ctypes  # noqa: PLC0415

        tier = _apply_process_awareness(ctypes.windll)
        logger.debug("process DPI awareness declared: %s", tier)
    except Exception:  # noqa: BLE001 — declaration is best-effort
        logger.warning("Could not set DPI awareness", exc_info=True)
    finally:
        _DPI_AWARENESS_SET = True


def pin_thread_dpi_per_monitor() -> bool:
    """Pin the CALLING thread's DPI context to PER_MONITOR_AWARE (Windows).

    Every top-level window the thread creates afterwards carries that context
    PER WINDOW, which gives a fixed-pixel overlay (the JarvisBar) exactly the
    stable behaviour the maintainer wants:

    - it renders its RAW pixels on every monitor (the bar's original look) —
      DWM never bitmap-scales it, so moving it onto a monitor with a different
      scale factor (100 % secondary next to the 150 % primary) no longer
      shrinks it to ~2/3 with a drag cursor offset;
    - a later PROCESS-level awareness flip (pywebview's ``webview.start()``
      calls ``user32.SetProcessDPIAware()`` at runtime,
      ``webview/platforms/winforms.py``) cannot re-interpret the window —
      no more mid-session size/position jumps.

    Deliberately PER_MONITOR_AWARE and NOT unaware: an UNAWARE pin makes
    Windows upscale the window (a blurry, oversized "fat bar" on a scaled
    display) — the maintainer rejected that look twice (2026-07-01 session,
    commit 5c7a5d15). Do not "fix" this by pinning UNAWARE again.

    The pin only survives a later process flip when the process is ALREADY
    DPI-aware, so call :func:`ensure_dpi_awareness` first. The context is
    deliberately NOT restored — call this only from a thread fully owned by
    the pinned surface (e.g. the bar's dedicated Tk mainloop thread).

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
        # Prefer PER_MONITOR_AWARE_V2 (-4, Win10 1703+); fall back to
        # PER_MONITOR_AWARE (-3, Win10 1607). Both prevent DWM bitmap scaling
        # and pin the context per-window; V2 additionally covers child windows
        # and non-client areas.
        for context in (-4, -3):
            prev = set_ctx(ctypes.c_void_p(context))
            if prev is not None:
                return True
        logger.debug("SetThreadDpiAwarenessContext(-4/-3) returned NULL")
        return False
    except (OSError, AttributeError):
        logger.debug("SetThreadDpiAwarenessContext unavailable", exc_info=True)
        return False
