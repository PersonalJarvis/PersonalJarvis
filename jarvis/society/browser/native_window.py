"""Full Chrome window capture for the isolated browser worker.

No Jarvis imports: this module runs inside the managed browser environment.
Window ownership is resolved by the browser PID returned by CDP, never by title.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import threading
import time
from typing import Any

log = logging.getLogger(__name__)
_capture_mta_lock = threading.Lock()
_capture_mta: tuple[Any, Any] | None = None
_PARKED_ALPHA = 0


def _release_capture_mta() -> None:
    global _capture_mta
    with _capture_mta_lock:
        retained, _capture_mta = _capture_mta, None
    if retained is not None:
        ole32, cookie = retained
        if ole32.CoDecrementMTAUsage(cookie) < 0:
            log.warning("Could not release the browser capture COM apartment")


def _ensure_capture_mta() -> None:
    """Keep cached WGC factories valid across this worker's capture sessions.

    windows-capture 2.0.1 drops its MTA cookie after each session. Its cached
    WinRT factories can then reference an unloaded apartment on the next
    start (upstream issue 124). Retain one cookie until this worker exits;
    individual windows still stop and join all capture and event threads.
    """
    global _capture_mta
    import atexit  # noqa: PLC0415
    import ctypes  # noqa: PLC0415

    with _capture_mta_lock:
        if _capture_mta is not None:
            return
        ole32 = ctypes.WinDLL("ole32")
        ole32.CoIncrementMTAUsage.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        ole32.CoIncrementMTAUsage.restype = ctypes.c_long
        ole32.CoDecrementMTAUsage.argtypes = [ctypes.c_void_p]
        ole32.CoDecrementMTAUsage.restype = ctypes.c_long
        cookie = ctypes.c_void_p()
        if ole32.CoIncrementMTAUsage(ctypes.byref(cookie)) < 0:
            raise RuntimeError("The browser capture COM apartment is unavailable")
        _capture_mta = ole32, cookie
        atexit.register(_release_capture_mta)


def available() -> bool:
    """Other hosts retain their existing page stream until native capture exists."""
    if os.name != "nt" or sys.platform != "win32":
        return False
    import ctypes  # noqa: PLC0415
    import importlib.util
    from ctypes import wintypes  # noqa: PLC0415

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
        self.failed = False
        self._closed = False
        self._parked = False
        if os.name != "nt":
            self.failed = True
            return
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        _ensure_capture_mta()
        from windows_capture import WindowsCapture  # type: ignore[import-not-found]

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.hwnd = 0
        self.pid = pid
        self.latest: dict[str, Any] | None = None
        self.lock = threading.RLock()
        self.input_lock = threading.RLock()
        self.ready = threading.Event()
        self._capture_factory = WindowsCapture
        self._captures: dict[int, tuple[Any, Any]] = {}
        self._capture_keys: dict[int, object] = {}
        self._restarted_popups: set[int] = set()
        self._images: dict[int, tuple[Any, float]] = {}
        self._popups: list[int] = []
        self._regions: list[dict[str, Any]] = []
        self._geometry_id = 0
        self._revision = 0
        self._encoded_revision = -1
        self._events: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
        self._event_latch = threading.Lock()
        self._windows_dirty = threading.Event()
        self._loop = asyncio.get_running_loop()
        self._event_task: asyncio.Task | None = None
        self._hooks: list[Any] = []
        self._hook_ready = threading.Event()
        self._hook_thread_id = 0
        self._hook_error: Exception | None = None
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
        u.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        u.ScreenToClient.restype = wintypes.BOOL
        u.ChildWindowFromPointEx.argtypes = [wintypes.HWND, wintypes.POINT, wintypes.UINT]
        u.ChildWindowFromPointEx.restype = wintypes.HWND
        u.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
        u.IsChild.restype = wintypes.BOOL
        u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetWindow.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
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
        self.input_hwnd = self.hwnd
        self.park()
        self._event_task = self._loop.create_task(self._consume_window_events())
        self._hook_thread = threading.Thread(
            target=self._watch_windows, name="jarvis-chrome-window-events", daemon=True
        )
        self._hook_thread.start()
        try:
            if not self._hook_ready.wait(2) or self._hook_error:
                raise RuntimeError("Chrome window events are unavailable") from self._hook_error
            self._start_capture(self.hwnd)
            self._sync_windows()
        except BaseException:
            self.close()
            raise

    def _watch_windows(self) -> None:
        """Window events invalidate the scene without polling the desktop."""
        if os.name != "nt":
            return
        c, w, u = self.ctypes, self.wintypes, self.user32
        initialized = False
        try:
            import pythoncom  # noqa: PLC0415

            pythoncom.CoInitialize()
            initialized = True
            kernel = c.WinDLL("kernel32")
            kernel.GetCurrentThreadId.restype = w.DWORD
            self._hook_thread_id = kernel.GetCurrentThreadId()
            # Ensure the queue exists before close() can post WM_QUIT.
            message = w.MSG()
            u.PeekMessageW(c.byref(message), None, 0, 0, 0)
            callback_type = c.WINFUNCTYPE(
                None, w.HANDLE, w.DWORD, w.HWND, c.c_long, c.c_long, w.DWORD, w.DWORD
            )

            @callback_type
            def callback(_hook, _event, hwnd, object_id, child_id, _thread, _time):
                if hwnd and object_id == 0 and child_id == 0 and not self._closed:
                    self._queue_window_event(int(hwnd))

            u.SetWinEventHook.argtypes = [
                w.DWORD, w.DWORD, w.HMODULE, callback_type, w.DWORD, w.DWORD, w.DWORD
            ]
            u.SetWinEventHook.restype = w.HANDLE
            u.UnhookWinEvent.argtypes = [w.HANDLE]
            u.UnhookWinEvent.restype = w.BOOL
            with self.lock:
                if self._closed:
                    return
                for first, last in ((0x8001, 0x8004), (0x800B, 0x800B)):
                    hook = u.SetWinEventHook(first, last, None, callback, self.pid, 0, 0)
                    if not hook:
                        raise RuntimeError("Chrome window event hook failed")
                    self._hooks.append(hook)
            self._hook_ready.set()
            pythoncom.PumpMessages()
        except Exception as exc:
            self._hook_error = exc
            log.exception("Chrome window event listener failed")
        finally:
            self._unhook_windows()
            self._hook_ready.set()
            if initialized:
                pythoncom.CoUninitialize()

    def _unhook_windows(self) -> None:
        with self.lock:
            failed = []
            for hook in self._hooks:
                if not self.user32.UnhookWinEvent(hook):
                    failed.append(hook)
                    log.warning("Could not release Chrome window event hook")
            self._hooks = failed

    async def _consume_window_events(self) -> None:
        """Drain the asyncio queue on its owning loop; rendering can use a pool."""
        while not self._closed:
            await self._events.get()
            self._windows_dirty.set()
            self._event_latch.release()

    def _queue_window_event(self, hwnd: int) -> None:
        # Native callbacks coalesce bursts into one queued invalidation. No
        # waiting, native calls, logging or scene work belongs on that thread.
        if not self._closed and self._event_latch.acquire(blocking=False):
            try:
                self._loop.call_soon_threadsafe(self._events.put_nowait, hwnd)
            except RuntimeError:
                # The application loop has already stopped during shutdown.
                self._event_latch.release()

    def _owned(self, hwnd: int) -> bool:
        """A matching PID alone must never grant access to another window."""
        if not hwnd:
            return False
        owner = self.wintypes.DWORD()
        u = self.user32
        u.GetWindowThreadProcessId(hwnd, self.ctypes.byref(owner))
        if owner.value != self.pid:
            return False
        root = int(u.GetAncestor(hwnd, 2) or hwnd)
        for _ in range(32):
            if root == self.hwnd:
                return True
            root = int(u.GetWindow(root, 4) or 0)  # GW_OWNER, not the child hierarchy.
            if not root:
                break
        return False

    def _sync_windows(self) -> None:
        popups = []

        @self.callback_type
        def visit(hwnd: int, _unused: int) -> bool:
            if hwnd != self.hwnd and self.user32.IsWindowVisible(hwnd) and self._owned(hwnd):
                popups.append(int(hwnd))
            return True

        self.user32.EnumWindows(visit, 0)
        if self._parked:
            for hwnd in popups:
                self._hide_from_desktop(hwnd)
        # EnumWindows enumerates front to back; painting uses the reverse order.
        popups.reverse()
        with self.lock:
            self._popups = popups
            self._revision += 1
            self._restarted_popups.intersection_update(popups)
        for hwnd in list(self._captures):
            if hwnd == self.hwnd:
                continue
            if hwnd not in popups:
                self._stop_capture(hwnd)
            elif hwnd not in self._capture_keys:
                if not self._stop_capture(hwnd):
                    self.failed = True
                    raise RuntimeError("The stopped Chrome popup capture could not be released")
                if hwnd in self._restarted_popups:
                    self.failed = True
                    raise RuntimeError("The Chrome popup capture repeatedly stopped")
                self._restarted_popups.add(hwnd)
                log.warning("Restarting a stopped Chrome popup capture")
        for hwnd in popups:
            if hwnd not in self._captures:
                try:
                    self._start_capture(hwnd)
                except Exception as exc:
                    if (
                        hwnd in self._restarted_popups
                        and self._owned(hwnd)
                        and self.user32.IsWindowVisible(hwnd)
                    ):
                        self.failed = True
                        raise RuntimeError("The Chrome popup capture could not restart") from exc
                    # Native menus can close between the show event and WGC
                    # startup; the main browser stream remains usable.
                    log.debug("Chrome popup capture could not start", exc_info=True)

    def _drain_window_events(self) -> None:
        if self._windows_dirty.is_set():
            self._windows_dirty.clear()
            self._sync_windows()

    def _start_capture(self, hwnd: int) -> None:
        capture = self._capture_factory(
            window_hwnd=hwnd,
            cursor_capture=False,
            draw_border=False,
            minimum_update_interval=66,
        )
        token = object()
        with self.lock:
            self._capture_keys[hwnd] = token

        @capture.event
        def on_frame_arrived(frame: Any, _control: Any) -> None:
            try:
                with self.lock:
                    if self._closed or self._capture_keys.get(hwnd) is not token:
                        return
                    # The WGC buffer belongs to the callback; retain a copy for
                    # compositing separately captured menu and dialog windows.
                    self._images[hwnd] = (frame.frame_buffer.copy(), time.time())
                    self._revision += 1
                if hwnd == self.hwnd:
                    self.ready.set()
            except Exception:
                if hwnd == self.hwnd:
                    self.failed = True
                    self.ready.set()
                log.exception("Native Chrome frame failed")

        @capture.event
        def on_closed() -> None:
            with self.lock:
                if self._closed or self._capture_keys.get(hwnd) is not token:
                    return
                if hwnd == self.hwnd:
                    self.failed = True
                    self.ready.set()
                    return
                # Invalidate pixels and late callbacks immediately. The owning
                # loop reaps the native worker before a bounded replacement;
                # stopping/joining it inside this callback would deadlock.
                self._capture_keys.pop(hwnd, None)
                self._images.pop(hwnd, None)
                self._revision += 1
            if not self._closed:
                self._queue_window_event(hwnd)

        try:
            self._captures[hwnd] = (capture, capture.start_free_threaded())
        except BaseException:
            with self.lock:
                self._capture_keys.pop(hwnd, None)
                self._images.pop(hwnd, None)
            raise

    def _stop_capture(self, hwnd: int) -> bool:
        with self.lock:
            self._capture_keys.pop(hwnd, None)
        capture = self._captures.pop(hwnd, None)
        stopped = threading.Event()
        if capture:
            def stop() -> None:
                try:
                    # windows-capture can join its native worker inside stop,
                    # so the deadline must enclose both operations.
                    capture[1].stop()
                    capture[1].wait()
                    stopped.set()
                except Exception:
                    log.warning("Chrome capture cleanup failed", exc_info=True)

            waiter = threading.Thread(
                target=stop, name="jarvis-chrome-capture-close", daemon=True
            )
            waiter.start()
            waiter.join(timeout=2.0)
            if waiter.is_alive():
                log.warning("Chrome capture did not exit before its deadline")
        with self.lock:
            self._images.pop(hwnd, None)
        return capture is None or stopped.is_set()

    def _bounds(self, hwnd: int) -> tuple[int, int, int, int]:
        c, w = self.ctypes, self.wintypes
        bounds = w.RECT()
        dwm = c.WinDLL("dwmapi")
        dwm.DwmGetWindowAttribute.argtypes = [w.HWND, w.DWORD, c.c_void_p, w.DWORD]
        dwm.DwmGetWindowAttribute.restype = c.c_long
        if dwm.DwmGetWindowAttribute(hwnd, 9, c.byref(bounds), c.sizeof(bounds)):
            if not self.user32.GetWindowRect(hwnd, c.byref(bounds)):
                raise RuntimeError("Chrome frame geometry is unavailable")
        return bounds.left, bounds.top, bounds.right, bounds.bottom

    def _requires_manual_control(self) -> bool:
        for hwnd in self._popups:
            if self._owned(hwnd) and self.user32.IsWindowVisible(hwnd):
                name = self.ctypes.create_unicode_buffer(128)
                self.user32.GetClassNameW(hwnd, name, len(name))
                # Common OS dialogs can expose arbitrary host files; their
                # pixels and controls stay on the manual product surface.
                if name.value == "#32770":
                    return True
        return False

    def frame(self) -> dict[str, Any] | None:
        with self.input_lock:
            return self._frame()

    def _frame(self) -> dict[str, Any] | None:
        if self.failed or self._closed:
            raise RuntimeError("The Chrome window capture stopped")
        if self._hook_error is not None:
            raise RuntimeError("Chrome window events stopped") from self._hook_error
        import cv2  # type: ignore[import-not-found]  # noqa: PLC0415

        self._drain_window_events()
        with self.dpi(), self.lock:
            if self._encoded_revision == self._revision:
                return dict(self.latest) if self.latest else None
            if self.hwnd not in self._images:
                return None
            base, timestamp = self._images[self.hwnd]
            canvas = base.copy()
            height, width = canvas.shape[:2]
            bounds = self._bounds(self.hwnd)
            regions = [{"hwnd": self.hwnd, "draw": (0, 0, width, height), "bounds": bounds}]
            for hwnd in self._popups:
                image = self._images.get(hwnd)
                if not image or not self._owned(hwnd) or not self.user32.IsWindowVisible(hwnd):
                    continue
                pixels, arrived = image
                rect = self._bounds(hwnd)
                ih, iw = pixels.shape[:2]
                scale = min(1.0, width / max(iw, 1), height / max(ih, 1))
                pw, ph = max(1, round(iw * scale)), max(1, round(ih * scale))
                # Chrome clamps menus to monitor edges. Preserve the whole
                # popup in the embedded canvas and invert this mapping on input.
                left = max(0, min(width - pw, rect[0] - bounds[0]))
                top = max(0, min(height - ph, rect[1] - bounds[1]))
                if scale != 1:
                    pixels = cv2.resize(pixels, (pw, ph))
                canvas[top : top + ph, left : left + pw] = pixels
                regions.append({"hwnd": hwnd, "draw": (left, top, pw, ph), "bounds": rect})
                timestamp = max(timestamp, arrived)
            ok, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if not ok:
                raise RuntimeError("Chrome window image encoding failed")
            if self._regions != regions:
                self._geometry_id += 1
            self._regions = regions
            self.latest = {
                "bytes": encoded.tobytes(), "timestamp": max(timestamp, time.time()),
                "width": width, "height": height, "geometry_id": str(self._geometry_id),
                "requires_manual_control": self._requires_manual_control(),
            }
            self._encoded_revision = self._revision
            return dict(self.latest)

    @contextlib.contextmanager
    def dpi(self):
        old = self.user32.SetThreadDpiAwarenessContext(self.ctypes.c_void_p(-4))
        try:
            yield
        finally:
            if old:
                self.user32.SetThreadDpiAwarenessContext(old)

    def _check_owner(self) -> None:
        if self._closed:
            raise RuntimeError("The Chrome window session is closed")
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

    def brand(self, icon_path: str) -> bool:
        """Mark the taskbar button with the Jarvis icon; never delay first pixels."""
        from uuid import UUID

        c, w, u = self.ctypes, self.wintypes, self.user32
        ole = c.WinDLL("ole32")
        ole.CoInitializeEx.argtypes = [c.c_void_p, w.DWORD]
        ole.CoInitializeEx.restype = c.c_long
        ole.CoCreateInstance.argtypes = [
            c.c_void_p,
            c.c_void_p,
            w.DWORD,
            c.c_void_p,
            c.POINTER(c.c_void_p),
        ]
        ole.CoCreateInstance.restype = c.c_long
        ole.CoUninitialize.argtypes = []
        u.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, c.c_int, c.c_int, w.UINT]
        u.LoadImageW.restype = w.HANDLE
        u.DestroyIcon.argtypes = [w.HICON]
        u.DestroyIcon.restype = w.BOOL
        initialized = ole.CoInitializeEx(None, 2)
        pointer = c.c_void_p()
        icon = None
        try:
            self._check_owner()
            clsid = c.create_string_buffer(UUID("56fdf344-fd6d-11d0-958a-006097c9a090").bytes_le)
            iid = c.create_string_buffer(UUID("ea1afb91-9e28-4b86-90e9-9e9f8a5eefaf").bytes_le)
            if ole.CoCreateInstance(clsid, None, 1, iid, c.byref(pointer)) < 0:
                raise RuntimeError("Taskbar icon interface is unavailable")
            table = c.cast(pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
            initialize = c.WINFUNCTYPE(c.c_long, c.c_void_p)(table[3])
            overlay = c.WINFUNCTYPE(c.c_long, c.c_void_p, w.HWND, w.HICON, w.LPCWSTR)(table[18])
            if initialize(pointer) < 0:
                raise RuntimeError("Taskbar icon initialization failed")
            icon = u.LoadImageW(None, icon_path, 1, 16, 16, 0x10)
            if not icon:
                raise RuntimeError("Jarvis browser icon could not be loaded")
            if overlay(pointer, self.hwnd, icon, "Jarvis Browser") < 0:
                raise RuntimeError("Jarvis browser badge could not be applied")
            return True
        except Exception:
            log.warning("Jarvis browser badge unavailable", exc_info=True)
            return False
        finally:
            if icon:
                u.DestroyIcon(icon)
            if pointer:
                table = c.cast(pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
                c.WINFUNCTYPE(w.ULONG, c.c_void_p)(table[2])(pointer)
            if initialized in (0, 1):
                ole.CoUninitialize()

    def post(self, message: int, key: int, value: int = 1) -> None:
        self.post_to(self.hwnd, message, key, value)

    def post_to(self, target: int, message: int, key: int, value: int = 1) -> None:
        self._check_owner()
        if not self._owned(target):
            raise RuntimeError("Chrome input target is no longer available")
        if not self.user32.PostMessageW(target, message, key, value):
            raise RuntimeError("Chrome could not receive this input")

    def click_target(self, x: int, y: int) -> tuple[int, int, int]:
        """Map captured frame pixels to the owned native widget under the point."""
        c, w, u = self.ctypes, self.wintypes, self.user32
        rendered = {region["hwnd"] for region in self._regions}
        if any(hwnd not in rendered and self._owned(hwnd) for hwnd in self._popups):
            raise RuntimeError("Chrome popup is not visible in the preview yet")
        for region in reversed(self._regions):
            left, top, width, height = region["draw"]
            if left <= x < left + width and top <= y < top + height:
                target = region["hwnd"]
                if not self._owned(target) or not u.IsWindowVisible(target):
                    raise RuntimeError("Chrome window changed; wait for a new frame")
                bounds = region["bounds"]
                if self._bounds(target) != bounds:
                    raise RuntimeError("Chrome window moved; wait for a new frame")
                screen_x = bounds[0] + round((x - left) * (bounds[2] - bounds[0]) / width)
                screen_y = bounds[1] + round((y - top) * (bounds[3] - bounds[1]) / height)
                break
        else:
            raise ValueError("Click is outside the Chrome window")
        for _ in range(16):
            point = w.POINT(screen_x, screen_y)
            if not u.ScreenToClient(target, c.byref(point)):
                raise RuntimeError("Chrome input coordinates are unavailable")
            # Chromium's render host can be transparent for composition while
            # still being the native keyboard/mouse input target.
            child = u.ChildWindowFromPointEx(target, point, 3)
            if not child or child == target:
                return target, point.x, point.y
            target = int(child)
        raise RuntimeError("Chrome input widget nesting is invalid")

    def viewport_point(self, x: float, y: float) -> tuple[float, float, int, int]:
        """Project CDP viewport coordinates into the complete captured Chrome frame."""
        c, w, u = self.ctypes, self.wintypes, self.user32
        self._check_owner()
        u.EnumChildWindows.argtypes = [w.HWND, self.callback_type, w.LPARAM]
        u.EnumChildWindows.restype = w.BOOL
        children = []

        @self.callback_type
        def visit(hwnd: int, _unused: int) -> bool:
            name = c.create_unicode_buffer(128)
            u.GetClassNameW(hwnd, name, len(name))
            if name.value == "Chrome_RenderWidgetHostHWND" and u.IsWindowVisible(hwnd):
                rect = w.RECT()
                if u.GetWindowRect(hwnd, c.byref(rect)):
                    children.append((rect.right - rect.left, rect.left, rect.top))
            return True

        with self.dpi():
            u.EnumChildWindows(self.hwnd, visit, 0)
            if not children:
                raise RuntimeError("Chrome viewport is unavailable")
            width, left, top = max(children)
            bounds = w.RECT()
            dwm = c.WinDLL("dwmapi")
            dwm.DwmGetWindowAttribute.argtypes = [w.HWND, w.DWORD, c.c_void_p, w.DWORD]
            dwm.DwmGetWindowAttribute.restype = c.c_long
            if dwm.DwmGetWindowAttribute(self.hwnd, 9, c.byref(bounds), c.sizeof(bounds)):
                raise RuntimeError("Chrome frame geometry is unavailable")
            frame = self.frame()
            if not frame:
                raise RuntimeError("Chrome frame is unavailable")
            scale = width / 1280
            return (
                left - bounds.left + x * scale,
                top - bounds.top + y * scale,
                frame["width"],
                frame["height"],
            )

    def _nonclient_point(self, target: int, x: int, y: int) -> tuple[int, int]:
        """Chrome's frame buttons use non-client messages, unlike its toolbar."""
        c, w, u = self.ctypes, self.wintypes, self.user32
        point = w.POINT(x, y)
        if not u.ClientToScreen(target, c.byref(point)):
            raise RuntimeError("Chrome frame coordinates are unavailable")
        position = ((point.y & 0xFFFF) << 16) | (point.x & 0xFFFF)
        result = c.c_size_t()
        u.SendMessageTimeoutW.argtypes = [
            w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, c.POINTER(c.c_size_t)
        ]
        u.SendMessageTimeoutW.restype = w.LPARAM
        if not u.SendMessageTimeoutW(target, 0x84, 0, position, 3, 200, c.byref(result)):
            raise RuntimeError("Chrome frame did not respond to input")
        return int(result.value), position

    def shortcut(self, key: int, modifiers: list[int]) -> None:
        """Apply modifiers to Chrome's input queue, never to the physical keyboard."""
        c, w, u = self.ctypes, self.wintypes, self.user32
        self._check_owner()
        if not self._owned(self.input_hwnd):
            raise RuntimeError("Chrome keyboard target is no longer available")
        kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentThreadId.restype = w.DWORD
        current = kernel.GetCurrentThreadId()
        target = u.GetWindowThreadProcessId(self.input_hwnd, None)
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
        if not u.AttachThreadInput(current, target, True):
            raise RuntimeError("Chrome keyboard focus is unavailable")
        saved_ok = False
        try:
            # AttachThreadInput resets keyboard state; save/restore the state
            # of the attached Chrome queue rather than our unrelated thread.
            if not u.GetKeyboardState(saved):
                raise RuntimeError("Chrome keyboard state is unavailable")
            saved_ok = True
            pressed = (w.BYTE * 256)(*saved)
            for modifier in modifiers:
                pressed[modifier] |= 0x80
            if not u.SetKeyboardState(pressed):
                raise RuntimeError("Chrome could not receive the keyboard modifiers")
            result = c.c_size_t()
            for message, bits in ((0x100, 1), (0x101, 0xC0000001)):
                if not u.SendMessageTimeoutW(
                    self.input_hwnd, message, key, bits, 3, 200, c.byref(result)
                ):
                    raise RuntimeError("Chrome did not respond to the keyboard shortcut")
        finally:
            if saved_ok and not u.SetKeyboardState(saved):
                log.warning("Could not restore Chrome keyboard state")
            if not u.AttachThreadInput(current, target, False):
                log.warning("Could not detach Chrome keyboard input")

    def _check_agent_target(self, target: int, args: dict) -> None:
        """Keep OS dialogs manual even when they appear after frame validation."""
        if not args.get("agent_input"):
            return
        if not self._owned(target):
            raise RuntimeError("Chrome input target is no longer available")
        current = target
        root = int(self.user32.GetAncestor(target, 2) or target)
        for _ in range(32):
            name = self.ctypes.create_unicode_buffer(128)
            if not self.user32.GetClassNameW(current, name, len(name)):
                raise RuntimeError("Chrome input target changed before dispatch")
            if name.value == "#32770":
                raise RuntimeError("Native OS dialogs require manual browser control")
            if current == self.hwnd:
                return
            current = root if current != root else int(self.user32.GetWindow(root, 4) or 0)
            root = current
            if not current or not self._owned(current):
                break
        raise RuntimeError("Chrome input target changed before dispatch")

    def input(self, op: str, args: dict) -> None:
        """Target Chrome only; never move the physical pointer or type globally."""
        if os.name != "nt":
            return
        with self.input_lock, self.dpi():
            self._check_owner()
            frame = self.frame()
            if not frame:
                raise RuntimeError("Chrome window image is unavailable")
            if args.get("geometry_id") is not None and (
                not frame or args["geometry_id"] != frame["geometry_id"]
            ):
                raise RuntimeError("Chrome window changed; wait for a new frame")
            rendered = {region["hwnd"] for region in self._regions}
            if any(hwnd not in rendered and self._owned(hwnd) for hwnd in self._popups):
                raise RuntimeError("Chrome popup is not visible in the preview yet")
            if op in {"click", "move", "scroll"}:
                if op == "scroll":
                    args = {"x": (frame or {}).get("width", 400) // 2,
                            "y": (frame or {}).get("height", 400) // 2, **args}
                x, y = int(args["x"]), int(args["y"])
                if not frame or not (0 <= x < frame["width"] and 0 <= y < frame["height"]):
                    raise ValueError("Click is outside the Chrome window")
                target, x, y = self.click_target(x, y)
                self._check_agent_target(target, args)
                position = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
                if op == "move":
                    self.post_to(target, 0x200, 0, position)
                    return
                if op == "scroll":
                    point = self.wintypes.POINT(x, y)
                    self.user32.ClientToScreen(target, self.ctypes.byref(point))
                    position = ((point.y & 0xFFFF) << 16) | (point.x & 0xFFFF)
                    for field, message, sign in (("dy", 0x20A, -1), ("dx", 0x20E, 1)):
                        delta = max(-1200, min(1200, int(sign * float(args.get(field, 0)))))
                        if delta:
                            self.post_to(target, message, (delta & 0xFFFF) << 16, position)
                    return
                button = str(args.get("button", "left"))
                buttons = {"left": (0x201, 0x202, 0x203, 1),
                           "right": (0x204, 0x205, 0x206, 2),
                           "middle": (0x207, 0x208, 0x209, 16)}
                if button not in buttons or args.get("count", 1) not in (1, 2):
                    raise ValueError("Unsupported Chrome mouse button or click count")
                down, up, double, state = buttons[button]
                self.input_hwnd = target
                # Activating the main HWND again dismisses its open profile
                # bubble. Focus belongs to the selected popup's own root.
                root = int(self.user32.GetAncestor(target, 2) or target)
                self.post_to(root, 0x6, 1, 0)
                self.post_to(target, 0x7, 0, 0)
                message = double if args.get("count", 1) == 2 else down
                if target == root:
                    hit, screen_position = self._nonclient_point(target, x, y)
                    if hit in {2, 3, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 20, 21}:
                        self.post_to(target, 0xA0, hit, screen_position)
                        self.post_to(target, message - 0x160, hit, screen_position)
                        self.post_to(target, up - 0x160, hit, screen_position)
                        return
                self.post_to(target, 0x200, 0, position)
                self.post_to(target, message, state, position)
                self.post_to(target, up, 0, position)
            elif op == "text":
                self._keyboard_target()
                self._check_agent_target(self.input_hwnd, args)
                self.post_to(self.input_hwnd, 0x7, 0, 0)
                encoded = str(args["text"]).encode("utf-16-le")
                for i in range(0, len(encoded), 2):
                    self.post_to(
                        self.input_hwnd, 0x102, int.from_bytes(encoded[i : i + 2], "little")
                    )
            elif op == "key":
                self._keyboard_target()
                self._check_agent_target(self.input_hwnd, args)
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
                    self.input_hwnd = self.hwnd
                    self.post(0x7, 0, 0)
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
                    self.post_to(self.input_hwnd, 0x100, key)
                    self.post_to(self.input_hwnd, 0x101, key, 0xC0000001)
            else:
                raise ValueError("Unsupported Chrome input operation")

    def _keyboard_target(self) -> None:
        """Native popup focus may change after a mouse or Tab event."""
        c, w, u = self.ctypes, self.wintypes, self.user32

        class GUIThreadInfo(c.Structure):
            _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD), ("hwndActive", w.HWND),
                        ("hwndFocus", w.HWND), ("hwndCapture", w.HWND), ("hwndMenuOwner", w.HWND),
                        ("hwndMoveSize", w.HWND), ("hwndCaret", w.HWND), ("rcCaret", w.RECT)]

        root = self._popups[-1] if self._popups else self.hwnd
        if not self._owned(root) or not u.IsWindowVisible(root):
            raise RuntimeError("Chrome keyboard target is no longer available")
        info = GUIThreadInfo(cbSize=c.sizeof(GUIThreadInfo))
        u.GetGUIThreadInfo.argtypes = [w.DWORD, c.POINTER(GUIThreadInfo)]
        u.GetGUIThreadInfo.restype = w.BOOL
        thread = u.GetWindowThreadProcessId(root, None)
        remembered_valid = self._owned(self.input_hwnd) and u.IsWindowVisible(self.input_hwnd)
        if (
            (self._popups or not remembered_valid) and u.GetGUIThreadInfo(thread, c.byref(info))
            and info.hwndFocus and self._owned(info.hwndFocus)
            and u.IsWindowVisible(info.hwndFocus)
            and int(u.GetAncestor(info.hwndFocus, 2) or info.hwndFocus) == root
        ):
            self.input_hwnd = int(info.hwndFocus)
        elif self._popups:
            self.input_hwnd = root
        elif not remembered_valid:
            raise RuntimeError("Chrome keyboard focus changed; select the input field again")

    def close(self) -> None:
        if os.name != "nt" or self._closed:
            return
        with self.input_lock:
            if self._closed:
                return
            self._closed = True
        if self._event_task is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._event_task.cancel)
        if self._hook_thread_id:
            u, w = self.user32, self.wintypes
            u.PostThreadMessageW.argtypes = [w.DWORD, w.UINT, w.WPARAM, w.LPARAM]
            u.PostThreadMessageW.restype = w.BOOL
            if not u.PostThreadMessageW(self._hook_thread_id, 0x12, 0, 0):
                log.warning("Could not stop Chrome window event message pump")
        self._hook_thread.join(timeout=2.0)
        if self._hook_thread.is_alive():
            log.warning("Chrome window event thread did not exit before its deadline")
        for hwnd in list(self._captures):
            self._stop_capture(hwnd)

    def _hide_from_desktop(self, hwnd: int) -> None:
        """Keep an owned window transparent while retaining native capture.

        Moving offscreen makes Chrome clamp its native menus onto a monitor.
        Hiding/minimizing stops window capture. Desktop alpha leaves the native
        surface and its coordinates intact; WGC captures the original pixels.
        The owning browser process retains this style until it exits.
        """
        if not self._owned(hwnd):
            raise RuntimeError("Chrome window ownership changed before hiding")
        c, w, u = self.ctypes, self.wintypes, self.user32
        u.GetWindowLongW.argtypes = [w.HWND, c.c_int]
        u.GetWindowLongW.restype = c.c_long
        u.SetWindowLongW.argtypes = [w.HWND, c.c_int, c.c_long]
        u.SetWindowLongW.restype = c.c_long
        u.SetLayeredWindowAttributes.argtypes = [w.HWND, w.DWORD, w.BYTE, w.DWORD]
        u.SetLayeredWindowAttributes.restype = w.BOOL
        u.GetLayeredWindowAttributes.argtypes = [
            w.HWND, c.POINTER(w.DWORD), c.POINTER(w.BYTE), c.POINTER(w.DWORD),
        ]
        u.GetLayeredWindowAttributes.restype = w.BOOL
        style = u.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE is a 32-bit style, not a pointer.
        # WGC rejects NOACTIVATE when APPWINDOW is absent. Use SWP_NOACTIVATE
        # in park() instead; the transparent surface also ignores desktop clicks.
        # Clear TOOLWINDOW on owned popups so they remain capturable too.
        hidden_style = (style | 0x00080020) & ~0x08040080
        if style != hidden_style:
            u.SetWindowLongW(hwnd, -20, hidden_style)
            if u.GetWindowLongW(hwnd, -20) != hidden_style:
                raise RuntimeError("The Chrome window could not be removed from the desktop")
        alpha, flags = w.BYTE(), w.DWORD()
        if (
            u.GetLayeredWindowAttributes(hwnd, None, c.byref(alpha), c.byref(flags))
            and flags.value & 0x2 and alpha.value == _PARKED_ALPHA
        ):
            return
        if not u.SetLayeredWindowAttributes(hwnd, 0, _PARKED_ALPHA, 0x2):  # LWA_ALPHA
            raise RuntimeError("The Chrome window could not be made transparent")

    def park(self) -> None:
        """Hide Chrome on the desktop while preserving the embedded window stream."""
        if os.name != "nt":
            return
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
            self._hide_from_desktop(self.hwnd)
            self._parked = True
            # Moving far offscreen breaks Chrome's monitor-clamped profile and
            # login bubbles. HWND_BOTTOM does not activate or move the browser.
            flags = 0x213
            c = self.ctypes

            class MonitorInfo(c.Structure):
                _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT),
                            ("rcWork", w.RECT), ("dwFlags", w.DWORD)]

            u.MonitorFromWindow.argtypes = [w.HWND, w.DWORD]
            u.MonitorFromWindow.restype = w.HANDLE
            u.GetMonitorInfoW.argtypes = [w.HANDLE, c.POINTER(MonitorInfo)]
            u.GetMonitorInfoW.restype = w.BOOL
            info = MonitorInfo(cbSize=c.sizeof(MonitorInfo))
            monitor = u.MonitorFromWindow(self.hwnd, 2)
            if not monitor or not u.GetMonitorInfoW(monitor, c.byref(info)):
                raise RuntimeError("Chrome monitor geometry is unavailable")
            left, top, right, bottom = self._bounds(self.hwnd)
            work = info.rcWork
            work_width, work_height = work.right - work.left, work.bottom - work.top
            width, height = right - left, bottom - top
            # Chrome restores its saved placement. Older parking positions and
            # crashed sessions leave thin strips or windows partly offscreen:
            # the preview then shows a sliver and menus clamp away from it.
            if width < min(work_width, 1024) or height < min(work_height, 720):
                width = min(work_width, max(width, round(work_width * 0.75)))
                height = min(work_height, max(height, round(work_height * 0.75)))
                flags &= ~0x1
            width, height = min(width, work_width), min(height, work_height)
            x = max(work.left, min(left, work.right - width))
            y = max(work.top, min(top, work.bottom - height))
            if (x, y) != (left, top):
                flags &= ~0x2
            if (width, height) != (right - left, bottom - top):
                flags &= ~0x1
            if not u.SetWindowPos(self.hwnd, 1, x, y, width, height, flags):
                raise RuntimeError("The Chrome window could not be placed in the background")
