"""Hold ESC to stop: a keyboard emergency stop next to the tray item.

Holding Escape for ``HOLD_S`` seconds fires the same callback as the tray's
emergency-stop item (``KillRequested``). A tap never fires — ESC is an
ordinary key in every app — and one hold fires once until the key is let go.

Windows reads the key with ``GetAsyncKeyState`` from a daemon thread (no hook,
nothing to unhook). Other platforms have no global key state without extra
permissions; there the watcher does not start and the tray/voice stops remain.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

HOLD_S = 1.5
POLL_S = 0.05
_VK_ESCAPE = 0x1B


class HoldDetector:
    """Pure edge logic: feed (is_down, now); True exactly once per long hold."""

    def __init__(self, hold_s: float = HOLD_S) -> None:
        self._hold_s = hold_s
        self._down_since: float | None = None
        self._fired = False

    def feed(self, is_down: bool, now: float) -> bool:
        if not is_down:
            self._down_since = None
            self._fired = False
            return False
        if self._down_since is None:
            self._down_since = now
        if not self._fired and now - self._down_since >= self._hold_s:
            self._fired = True
            return True
        return False


def _escape_down() -> bool:
    import ctypes  # noqa: PLC0415 - Windows only

    return bool(ctypes.windll.user32.GetAsyncKeyState(_VK_ESCAPE) & 0x8000)


def start_esc_hold_watcher(on_hold: Callable[[], None]) -> threading.Thread | None:
    """Start the watcher thread; None where the platform cannot read keys."""
    if sys.platform != "win32":
        log.info("ESC-hold stop unavailable on %s; tray and voice stop remain", sys.platform)
        return None

    def _run() -> None:
        detector = HoldDetector()
        while True:
            try:
                if detector.feed(_escape_down(), time.monotonic()):
                    log.info("ESC held %.1fs: emergency stop", HOLD_S)
                    on_hold()
            except Exception:  # noqa: BLE001 - the watcher must outlive one bad poll
                log.warning("ESC-hold poll failed", exc_info=True)
            time.sleep(POLL_S)

    thread = threading.Thread(target=_run, name="jarvis-esc-hold", daemon=True)
    thread.start()
    return thread


__all__ = ["HOLD_S", "HoldDetector", "start_esc_hold_watcher"]
