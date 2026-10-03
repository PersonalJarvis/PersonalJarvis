"""An explicit, reversible dock for the user's ordinary browser window.

The browser remains an independent top-level window. Reparenting a window
from the user's shared Chrome process would couple its input, DPI and crash
lifecycle to our host. Moving the selected window leaves that process and its
profile alone. Nothing here opens a debugging port or reads browser storage.
"""

from __future__ import annotations

import logging
import os
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)
LEASE_SECONDS = 8.0


@dataclass(frozen=True)
class BrowserWindow:
    handle: int
    pid: int
    title: str


@dataclass(frozen=True)
class Viewport:
    x: float
    y: float
    width: float
    height: float
    viewport_width: float
    viewport_height: float

    def __post_init__(self) -> None:
        import math

        values = (self.x, self.y, self.width, self.height,
                  self.viewport_width, self.viewport_height)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Non-finite browser bounds")
        if (min(self.x, self.y) < 0 or min(values[2:]) <= 0
                or self.x + self.width > self.viewport_width + 1
                or self.y + self.height > self.viewport_height + 1):
            raise ValueError("Browser bounds must fit the host viewport")

    def pixels(self, origin: tuple[int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
        sx, sy = size[0] / self.viewport_width, size[1] / self.viewport_height
        return (origin[0] + round(self.x * sx), origin[1] + round(self.y * sy),
                round(self.width * sx), round(self.height * sy))


def default_browser_executable() -> Path | None:
    """Ask Windows for its HTTPS handler executable, never parse shell commands."""
    if os.name != "nt":
        return None
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes as w  # noqa: PLC0415

    api = ctypes.WinDLL("shlwapi", use_last_error=True)
    fn = api.AssocQueryStringW
    fn.argtypes = [w.DWORD, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    fn.restype = ctypes.c_long
    size = w.DWORD(32768)
    buf = ctypes.create_unicode_buffer(size.value)
    # ASSOCF_IS_PROTOCOL / ASSOCSTR_EXECUTABLE, using the default user's choice.
    if fn(0x1000, 2, "https", "open", buf, ctypes.byref(size)) != 0:
        return None
    path = Path(buf.value)
    return path if path.is_absolute() and path.is_file() else None


class SystemBrowserSession:
    """One desktop host, one explicitly selected window, bounded presence lease."""

    def __init__(self, host: Any, *, native: Any = None, executable: Path | None = None,
                 clock: Any = time.monotonic) -> None:
        self.host = host
        self.native = native
        self.executable = executable or default_browser_executable()
        self.clock = clock
        self.lock = threading.RLock()
        self.candidates: dict[str, BrowserWindow] = {}
        self.selected: BrowserWindow | None = None
        self.placement: Any = None
        self.bounds: Viewport | None = None
        self.lease: str | None = None
        self.deadline = 0.0
        self.watcher: Any = None

    def status(self) -> dict[str, Any]:
        with self.lock:
            supported = bool(self.executable and self.executable.name.lower() == "chrome.exe")
            return {"available": bool(self.executable), "can_dock": supported,
                    "browser": "Chrome" if supported else "Default browser",
                    "docked": self.selected is not None,
                    "reason": "" if supported else "chrome_windows_only"}

    def open(self) -> dict[str, Any]:
        if not self.executable:
            return {"ok": False, "reason": "browser_unavailable"}
        # No URL or profile switch: Chrome retains its normal startup/profile picker.
        subprocess.Popen([str(self.executable)], creationflags=NO_WINDOW_CREATIONFLAGS,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, encoding="utf-8")
        return {"ok": True}

    def windows(self) -> dict[str, Any]:
        with self.lock:
            if not self.status()["can_dock"]:
                return {"windows": [], **self.status()}
            api = self._native()
            self.candidates = {secrets.token_urlsafe(18): window
                               for window in api.windows(self.executable)}
            return {"windows": [{"id": token, "title": window.title}
                                for token, window in self.candidates.items()], **self.status()}

    def attach(self, token: str, bounds: Viewport) -> dict[str, Any]:
        with self.lock:
            target = self.candidates.get(token)
            if target is None or not self._native().matches(target, self.executable):
                return {"ok": False, "reason": "window_gone"}
            if self.selected is not None:
                return {"ok": False, "reason": "already_docked"}
            self.placement = self.native.snapshot(target)
            self.selected, self.bounds = target, bounds
            self.lease = secrets.token_urlsafe(24)
            self.deadline = self.clock() + LEASE_SECONDS
            try:
                self.watcher = self.native.watch(self._event)
                self.native.place(target, bounds, raise_window=True)
            except Exception:
                self._restore()
                raise
            return {"ok": True, "lease": self.lease}

    def present(self, lease: str, bounds: Viewport) -> dict[str, Any]:
        with self.lock:
            if self.selected is None or lease != self.lease:
                return {"ok": False, "reason": "session_ended"}
            if not getattr(self.watcher, "alive", True):
                self._restore()
                return {"ok": False, "reason": "session_ended"}
            if not self.native.matches(self.selected, self.executable):
                self._restore()
                return {"ok": False, "reason": "window_gone"}
            self.bounds = bounds
            self.deadline = self.clock() + LEASE_SECONDS
            self.native.place(self.selected, bounds, raise_window=False)
            return {"ok": True}

    def detach(self, lease: str) -> dict[str, Any]:
        with self.lock:
            if lease != self.lease:
                return {"ok": False, "reason": "session_ended"}
            self._restore()
            return {"ok": True}

    def close(self, *_: Any) -> None:
        with self.lock:
            self._restore()

    def _restore(self) -> None:
        selected, placement, watcher = self.selected, self.placement, self.watcher
        self.selected = self.placement = self.bounds = self.lease = self.watcher = None
        if watcher is not None:
            watcher.stop()
        if selected is not None and self.native.matches(selected, self.executable):
            self.native.restore(selected, placement)

    def _event(self, kind: str) -> None:
        # Called by the watcher's message loop, never inside a WinEvent callback.
        # Do not block its shutdown behind a route which is joining this worker.
        if not self.lock.acquire(blocking=False):
            return
        try:
            if self.selected is None:
                return
            if (kind == "host_gone" or self.clock() > self.deadline
                    or not self.native.matches(self.selected, self.executable)):
                self._restore()
            elif kind in ("host_moved", "host_focused") and self.bounds:
                self.native.place(self.selected, self.bounds, raise_window=kind == "host_focused")
        except Exception:
            log.exception("Could not update the system browser dock")
            self._restore()
        finally:
            self.lock.release()

    def _native(self) -> Any:
        if self.native is None:
            from jarvis.ui.system_browser_win32 import BrowserDock  # noqa: PLC0415
            from jarvis.ui.window_frame import native_hwnd  # noqa: PLC0415

            hwnd = native_hwnd(self.host)
            if not hwnd:
                raise RuntimeError("Desktop window is unavailable")
            self.native = BrowserDock(hwnd)
        return self.native
