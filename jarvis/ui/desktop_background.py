"""Session-only state for the desktop shell's explicit background lifecycle."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Literal, TypedDict


class BackgroundStatus(TypedDict):
    available: bool
    reason: str | None
    enabled: bool
    mode: Literal["foreground", "background", "stopping", "stopped"]
    window_open: bool


class BackgroundSession:
    """Own only the blank GUI keeper; never own execution or its subscriptions."""

    def __init__(self) -> None:
        self.enabled = False
        self.keeper: Any = None
        self.backend_failed = False
        self.transition = threading.Lock()
        self.keeper_lock = threading.Lock()

    @property
    def alive(self) -> bool:
        keeper = self.keeper
        return keeper is not None and not keeper.events.closed.is_set()


def _query_notification_icon_rect(hwnd: int, icon_id: int, shell32: Any) -> bool:
    """Marshal the documented shell API; the injected DLL keeps CI native-free."""
    import ctypes  # noqa: PLC0415

    class Identifier(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint32),
            ("hWnd", ctypes.c_void_p),
            ("uID", ctypes.c_uint32),
            ("guidItem", ctypes.c_ubyte * 16),
        ]

    class Rect(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_int32), ("top", ctypes.c_int32),
            ("right", ctypes.c_int32), ("bottom", ctypes.c_int32),
        ]

    query = shell32.Shell_NotifyIconGetRect
    # Stable pointer argtypes also allow two independently owned tray probes
    # to share the cached DLL without replacing each other's local struct types.
    query.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    query.restype = ctypes.c_int32
    identifier = Identifier(cbSize=ctypes.sizeof(Identifier), hWnd=hwnd, uID=icon_id)
    rect = Rect()
    return (
        query(ctypes.byref(identifier), ctypes.byref(rect)) == 0
        and rect.right > rect.left and rect.bottom > rect.top
    )


@lru_cache(maxsize=1)
def _native_shell32() -> Any:
    if os.name != "nt":
        return None
    import ctypes  # noqa: PLC0415

    return ctypes.WinDLL("shell32", use_last_error=True)


def native_tray_present(icon: Any) -> bool:
    """Require a shell-owned rectangle, not pystray's optimistic visible flag.

    Windows borrows the tray's HWND; no hook or native handle is allocated.
    Other OSes keep their ordinary tray but decline background ownership until
    an equivalent native registration/presence acknowledgment is implemented.
    """
    if os.name != "nt":
        return False
    hwnd = getattr(icon, "_hwnd", None)
    if not hwnd:
        return False
    try:
        shell32 = _native_shell32()
        # pystray 0.19 uses uID=0; accept the object ID spelling used by some
        # backend variants too, always under this icon's own callback HWND.
        return any(
            _query_notification_icon_rect(int(hwnd), icon_id, shell32)
            for icon_id in (0, id(icon) & 0xFFFFFFFF)
        )
    except (AttributeError, OSError, TypeError, ValueError):
        logging.getLogger(__name__).debug("Native tray presence unavailable", exc_info=True)
        return False


class TrayPresenceProbe:
    """At most one native query in flight, with a bounded caller wait and stop."""

    def __init__(self, query: Callable[[Any], bool] = native_tray_present) -> None:
        self._query = query
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._result: list[bool] = []
        self._icon: Any = None
        self._stopped = False

    def check(self, icon: Any, *, timeout: float = 0.25) -> bool:
        with self._lock:
            if self._stopped:
                return False
            thread = self._thread
            if thread is not None and thread.is_alive() and self._icon is not icon:
                return False
            if thread is None or not thread.is_alive():
                result: list[bool] = []

                def _query() -> None:
                    try:
                        result.append(bool(self._query(icon)))
                    except Exception:  # noqa: BLE001
                        logging.getLogger(__name__).warning(
                            "Tray presence query failed", exc_info=True,
                        )
                        result.append(False)

                thread = threading.Thread(target=_query, name="jarvis-tray-presence", daemon=True)
                self._thread = thread
                self._result = result
                self._icon = icon
                thread.start()
            result = self._result
        thread.join(timeout=timeout)
        return not self._stopped and not thread.is_alive() and result == [True]

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
            if thread.is_alive():
                logging.getLogger(__name__).warning(
                    "Native tray query did not stop within two seconds",
                )

    def resume(self) -> None:
        """Never abandon a hung native query in order to start another one."""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stopped = False


def native_background_reason(window: Any) -> str | None:
    """Probe the running GUI, not merely whether pywebview can be imported."""
    if window is None or getattr(window, "gui", None) is None:
        return "native_window_unavailable"
    # GTK's runtime show() ignores hidden=True once its main loop is running.
    # Keep the ordinary window/headless paths usable until that backend has a
    # verified invisible-keeper lifecycle; do not flash a window as a workaround.
    if getattr(window.gui, "renderer", None) == "gtkwebkit2":
        return "hidden_window_unavailable"
    shown = getattr(getattr(window, "events", None), "shown", None)
    if shown is None or not shown.is_set():
        return "native_window_not_ready"
    return None
