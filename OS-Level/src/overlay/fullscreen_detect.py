"""SHQueryUserNotificationState polling. Plan §12.6 + §20.2.

Polls every 2 seconds on a daemon thread with
``THREAD_PRIORITY_LOWEST`` (best effort). When the state changes to a
"hide-worthy" code (D3D fullscreen, presentation mode, or optionally
busy), it fires a callback. A classic pull-polling approach because
Windows has no push event for this status.

No PySide6 dependency, so the polling thread can also run in test
setups without a QApplication.
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# Plan §12.6 — SHQueryUserNotificationState Codes (winuser.h).
class UserNotificationState(IntEnum):
    NOT_PRESENT = 1  # QUNS_NOT_PRESENT
    BUSY = 2  # QUNS_BUSY
    RUNNING_D3D_FULL_SCREEN = 3  # QUNS_RUNNING_D3D_FULL_SCREEN
    PRESENTATION_MODE = 4  # QUNS_PRESENTATION_MODE
    ACCEPTS_NOTIFICATIONS = 5  # QUNS_ACCEPTS_NOTIFICATIONS
    QUIET_TIME = 6  # QUNS_QUIET_TIME
    APP = 7  # QUNS_APP


# Plan §12.6: default hide codes (BUSY is optional via ignore_busy_state).
_HARD_HIDE_STATES: frozenset[UserNotificationState] = frozenset(
    {UserNotificationState.RUNNING_D3D_FULL_SCREEN, UserNotificationState.PRESENTATION_MODE}
)
_SOFT_HIDE_STATES: frozenset[UserNotificationState] = frozenset(
    {UserNotificationState.BUSY}
)


@dataclass(frozen=True)
class FullscreenStatus:
    """Snapshot of the last polling tick."""

    state: UserNotificationState
    should_hide: bool


# Callback signature: (FullscreenStatus) -> None.
StatusCallback = Callable[[FullscreenStatus], None]


def query_state() -> Optional[UserNotificationState]:
    """Plan §12.6 — SHQueryUserNotificationState via ctypes.

    Returns None on non-Windows or when the API isn't available.
    """
    if sys.platform != "win32":
        return None
    import ctypes

    try:
        sh = ctypes.windll.shell32
    except (OSError, AttributeError):  # pragma: no cover
        return None

    sh.SHQueryUserNotificationState.restype = ctypes.c_long
    out = ctypes.c_int(0)
    hr = sh.SHQueryUserNotificationState(ctypes.byref(out))
    if hr != 0:
        logger.debug("SHQueryUserNotificationState HRESULT=%d", hr)
        return None
    try:
        return UserNotificationState(out.value)
    except ValueError:
        logger.debug("Unknown UserNotificationState: %d", out.value)
        return None


def should_hide_for_state(
    state: UserNotificationState, *, ignore_busy_state: bool
) -> bool:
    """Mapping state -> hide. Plan §12.6:
    - always hide on RUNNING_D3D_FULL_SCREEN, PRESENTATION_MODE
    - hide on BUSY only when ignore_busy_state == False (default)
    """
    if state in _HARD_HIDE_STATES:
        return True
    if state in _SOFT_HIDE_STATES and not ignore_busy_state:
        return True
    return False


class FullscreenDetector:
    """Polling thread around SHQueryUserNotificationState.

    Plan §12.6: low-prio daemon thread, 2 s interval, clean exit.

    Lifecycle::

        det = FullscreenDetector(callback=on_change)
        det.start()
        ...
        det.stop()
    """

    def __init__(
        self,
        *,
        callback: Optional[StatusCallback] = None,
        poll_interval_s: float = 2.0,
        ignore_busy_state: bool = False,
        query_fn: Callable[[], Optional[UserNotificationState]] = query_state,
    ) -> None:
        self._callback = callback
        self._poll_interval = poll_interval_s
        self._ignore_busy_state = ignore_busy_state
        self._query_fn = query_fn
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_status: Optional[FullscreenStatus] = None

    @property
    def last_status(self) -> Optional[FullscreenStatus]:
        return self._last_status

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_ignore_busy_state(self, ignore: bool) -> None:
        """Runtime toggle (e.g. when a config reload happens)."""
        self._ignore_busy_state = ignore

    def set_callback(self, callback: Optional[StatusCallback]) -> None:
        self._callback = callback

    def poll_once(self) -> Optional[FullscreenStatus]:
        """Synchronous poll cycle. Returns FullscreenStatus or None
        (non-Windows / API error)."""
        state = self._query_fn()
        if state is None:
            return None
        status = FullscreenStatus(
            state=state,
            should_hide=should_hide_for_state(
                state, ignore_busy_state=self._ignore_busy_state
            ),
        )
        prev = self._last_status
        self._last_status = status
        if self._callback is not None and (
            prev is None or prev.state is not status.state or prev.should_hide is not status.should_hide
        ):
            try:
                self._callback(status)
            except Exception:  # noqa: BLE001
                logger.exception("FullscreenDetector callback raised")
        return status

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="overlay-fullscreen-detect", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        # Best effort: lower the thread priority on Windows.
        if sys.platform == "win32":
            try:
                import ctypes

                THREAD_PRIORITY_LOWEST = -2
                handle = ctypes.windll.kernel32.GetCurrentThread()
                ctypes.windll.kernel32.SetThreadPriority(handle, THREAD_PRIORITY_LOWEST)
            except Exception:  # noqa: BLE001
                logger.debug("SetThreadPriority failed", exc_info=True)

        while not self._stop.is_set():
            self.poll_once()
            if self._stop.wait(timeout=self._poll_interval):
                return


__all__ = [
    "FullscreenDetector",
    "FullscreenStatus",
    "StatusCallback",
    "UserNotificationState",
    "query_state",
    "should_hide_for_state",
]
