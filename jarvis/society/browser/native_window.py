"""Full Chrome window capture for the isolated browser worker.

No Jarvis imports: this module runs inside the managed browser environment.
Window ownership is resolved by the browser PID returned by CDP, never by title.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import time
from typing import Any

log = logging.getLogger(__name__)


def available() -> bool:
    """Other hosts retain their existing page stream until native capture exists."""
    if sys.platform != "win32":
        return False
    import ctypes
    import importlib.util
    from ctypes import wintypes

    if importlib.util.find_spec("windows_capture") is None:
        return False
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    u.OpenInputDesktop.restype = wintypes.HANDLE
    u.CloseDesktop.argtypes = [wintypes.HANDLE]
    u.CloseDesktop.restype = wintypes.BOOL
    desktop = u.OpenInputDesktop(0, False, 1)
    if not desktop:
        return False
    u.CloseDesktop(desktop)
    return True


class NativeWindow:
    def __init__(self, pid: int) -> None:
        import ctypes
        from ctypes import wintypes

        from windows_capture import WindowsCapture  # type: ignore[import-not-found]

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.hwnd = 0
        self.pid = pid
        self.latest: dict[str, Any] | None = None
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.failed = False
        u = self.user32
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows.argtypes = [self.callback_type, wintypes.LPARAM]
        u.EnumWindows.restype = wintypes.BOOL
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindowVisible.restype = wintypes.BOOL
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetWindowRect.restype = wintypes.BOOL
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW.restype = ctypes.c_int
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.PostMessageW.restype = wintypes.BOOL
        u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        u.ClientToScreen.restype = wintypes.BOOL
        u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p

        candidates: list[tuple[int, int]] = []

        @self.callback_type
        def visit(hwnd: int, _unused: int) -> bool:
            owner = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            name = ctypes.create_unicode_buffer(128)
            u.GetClassNameW(hwnd, name, len(name))
            if (
                owner.value == pid
                and u.IsWindowVisible(hwnd)
                and name.value == "Chrome_WidgetWin_1"
            ):
                rect = wintypes.RECT()
                if u.GetWindowRect(hwnd, ctypes.byref(rect)):
                    candidates.append(
                        ((rect.right - rect.left) * (rect.bottom - rect.top), int(hwnd))
                    )
            return True

        u.EnumWindows(visit, 0)
        if candidates:
            # Chrome's restore bubbles and menus use the same window class.
            # Select the full owned browser, never one of those small popups.
            self.hwnd = max(candidates)[1]
        if not self.hwnd:
            raise RuntimeError("The owned Chrome window is unavailable")
        capture = WindowsCapture(
            window_hwnd=self.hwnd,
            cursor_capture=False,
            draw_border=False,
            minimum_update_interval=66,
        )

        @capture.event
        def on_frame_arrived(frame: Any, _control: Any) -> None:
            import cv2  # type: ignore[import-not-found]

            try:
                captured_at = time.time()
                ok, encoded = cv2.imencode(
                    ".jpg", frame.frame_buffer, [cv2.IMWRITE_JPEG_QUALITY, 75]
                )
                if not ok:
                    raise RuntimeError("Chrome window image encoding failed")
                with self.lock:
                    self.latest = {
                        "bytes": encoded.tobytes(),
                        "timestamp": captured_at,
                        "width": frame.width,
                        "height": frame.height,
                    }
                self.ready.set()
            except Exception:
                self.failed = True
                self.ready.set()
                log.exception("Native Chrome frame failed")

        @capture.event
        def on_closed() -> None:
            self.failed = True
            self.ready.set()

        self.capture = capture
        self.control = capture.start_free_threaded()

    def frame(self) -> dict[str, Any] | None:
        if self.failed:
            raise RuntimeError("The Chrome window capture stopped")
        with self.lock:
            return dict(self.latest) if self.latest else None

    @contextlib.contextmanager
    def dpi(self):
        old = self.user32.SetThreadDpiAwarenessContext(self.ctypes.c_void_p(-4))
        try:
            yield
        finally:
            if old:
                self.user32.SetThreadDpiAwarenessContext(old)

    def _check_owner(self) -> None:
        owner = self.wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(self.hwnd, self.ctypes.byref(owner))
        if owner.value != self.pid:
            raise RuntimeError("The owned Chrome window has closed")

    def title(self) -> str:
        self._check_owner()
        u, w, c = self.user32, self.wintypes, self.ctypes
        u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
        u.GetWindowTextW.restype = c.c_int
        title = c.create_unicode_buffer(4096)
        u.GetWindowTextW(self.hwnd, title, len(title))
        return title.value

    def post(self, message: int, key: int, value: int = 1) -> None:
        self._check_owner()
        if not self.user32.PostMessageW(self.hwnd, message, key, value):
            raise RuntimeError("Chrome could not receive this input")

    def shortcut(self, key: int, modifiers: list[int]) -> None:
        """Apply modifiers to Chrome's input queue, never to the physical keyboard."""
        c, w, u = self.ctypes, self.wintypes, self.user32
        kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentThreadId.restype = w.DWORD
        current = kernel.GetCurrentThreadId()
        target = u.GetWindowThreadProcessId(self.hwnd, None)
        u.AttachThreadInput.argtypes = [w.DWORD, w.DWORD, w.BOOL]
        u.AttachThreadInput.restype = w.BOOL
        u.GetKeyboardState.argtypes = [c.POINTER(w.BYTE)]
        u.SetKeyboardState.argtypes = [c.POINTER(w.BYTE)]
        u.SendMessageTimeoutW.argtypes = [
            w.HWND,
            w.UINT,
            w.WPARAM,
            w.LPARAM,
            w.UINT,
            w.UINT,
            c.POINTER(c.c_size_t),
        ]
        u.SendMessageTimeoutW.restype = w.LPARAM
        saved = (w.BYTE * 256)()
        if not u.GetKeyboardState(saved):
            raise RuntimeError("Chrome keyboard state is unavailable")
        if not u.AttachThreadInput(current, target, True):
            raise RuntimeError("Chrome keyboard focus is unavailable")
        try:
            pressed = (w.BYTE * 256)(*saved)
            for modifier in modifiers:
                pressed[modifier] |= 0x80
            if not u.SetKeyboardState(pressed):
                raise RuntimeError("Chrome could not receive the keyboard modifiers")
            result = c.c_size_t()
            for message, bits in ((0x100, 1), (0x101, 0xC0000001)):
                if not u.SendMessageTimeoutW(
                    self.hwnd, message, key, bits, 3, 200, c.byref(result)
                ):
                    raise RuntimeError("Chrome did not respond to the keyboard shortcut")
        finally:
            if not u.SetKeyboardState(saved):
                log.warning("Could not restore Chrome keyboard state")
            if not u.AttachThreadInput(current, target, False):
                log.warning("Could not detach Chrome keyboard input")

    def input(self, op: str, args: dict) -> None:
        """Target Chrome only; never move the physical pointer or type globally."""
        with self.dpi():
            self._check_owner()
            # Deliver focus to Chrome's widget without changing OS foreground
            # focus; otherwise its background omnibox can discard typed text.
            self.post(0x6, 1, 0)
            self.post(0x7, 0, 0)
            if op == "click":
                x, y = int(args["x"]), int(args["y"])
                frame = self.frame()
                if not frame or not (0 <= x < frame["width"] and 0 <= y < frame["height"]):
                    raise ValueError("Click is outside the Chrome window")
                self.post(0x200, 0, (y << 16) | (x & 0xFFFF))
                self.post(0x201, 1, (y << 16) | (x & 0xFFFF))
                self.post(0x202, 0, (y << 16) | (x & 0xFFFF))
            elif op == "text":
                encoded = str(args["text"]).encode("utf-16-le")
                for i in range(0, len(encoded), 2):
                    self.post(0x102, int.from_bytes(encoded[i : i + 2], "little"))
            elif op == "key":
                # Chromium's chrome/app/chrome_command_ids.h: dispatch the
                # native browser commands without a shared modifier-key state.
                commands = {
                    "Control+t": 34014,
                    "Control+w": 34015,
                    "Control+l": 39001,
                    "Control+r": 33002,
                    "Control+Tab": 34016,
                    "Control+Shift+Tab": 34017,
                }
                if args["key"] in commands:
                    self.post(0x111, commands[args["key"]], 0)
                    return
                keys = {
                    "Enter": 13,
                    "Tab": 9,
                    "Backspace": 8,
                    "Delete": 46,
                    "Escape": 27,
                    "ArrowLeft": 37,
                    "ArrowUp": 38,
                    "ArrowRight": 39,
                    "ArrowDown": 40,
                    "Home": 36,
                    "End": 35,
                    "PageUp": 33,
                    "PageDown": 34,
                }
                parts = str(args["key"]).split("+")
                modifiers = {"Control": 17, "Shift": 16, "Alt": 18}
                if any(part not in modifiers for part in parts[:-1]):
                    raise ValueError("Unsupported Chrome keyboard modifier")
                key_name = parts[-1]
                key = keys.get(key_name)
                if key is None and len(key_name) == 1 and key_name.isascii():
                    key = ord(key_name.upper())
                if key is None:
                    raise ValueError("Unsupported Chrome key")
                if len(parts) > 1:
                    self.shortcut(key, [modifiers[part] for part in parts[:-1]])
                else:
                    self.post(0x100, key)
                    self.post(0x101, key, 0xC0000001)
            elif op == "scroll":
                delta = max(-1200, min(1200, int(-float(args.get("dy", 0)))))
                point = self.wintypes.POINT(200, 200)
                self.user32.ClientToScreen(self.hwnd, self.ctypes.byref(point))
                self.post(
                    0x20A, (delta & 0xFFFF) << 16, ((point.y & 0xFFFF) << 16) | (point.x & 0xFFFF)
                )

    def close(self) -> None:
        self.control.stop()
        self.control.wait()

    def park(self) -> None:
        """Keep the owned window outside the user's working area, still rendered."""
        u, w = self.user32, self.wintypes
        u.SetWindowPos.argtypes = [
            w.HWND,
            w.HWND,
            self.ctypes.c_int,
            self.ctypes.c_int,
            self.ctypes.c_int,
            self.ctypes.c_int,
            w.UINT,
        ]
        u.SetWindowPos.restype = w.BOOL
        with self.dpi():
            self._check_owner()
            if not u.SetWindowPos(self.hwnd, 1, -16000, -16000, 0, 0, 0x11):
                raise RuntimeError("The Chrome window could not be placed in the background")
