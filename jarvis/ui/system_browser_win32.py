"""Lazy Win32 window positioning for the ordinary, user-selected Chrome window."""

from __future__ import annotations

import logging
import os
import queue
import threading
from pathlib import Path
from typing import Any

from jarvis.core.win32_dpi import per_monitor_dpi_context
from jarvis.ui.system_browser import BrowserWindow, Viewport

log = logging.getLogger(__name__)


class BrowserDock:
    def __init__(self, host: int) -> None:
        if os.name != "nt":
            raise RuntimeError("Native browser docking is only available on Windows")
        import ctypes as c  # noqa: PLC0415
        from ctypes import wintypes as w  # noqa: PLC0415

        self.c, self.w, self.host = c, w, host
        self.u = c.WinDLL("user32", use_last_error=True)
        self.k = c.WinDLL("kernel32", use_last_error=True)
        self.enum_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.hook_type = c.WINFUNCTYPE(None, w.HANDLE, w.DWORD, w.HWND,
                                     w.LONG, w.LONG, w.DWORD, w.DWORD)

        class Placement(c.Structure):
            _fields_ = [("length", w.UINT), ("flags", w.UINT), ("showCmd", w.UINT),
                        ("ptMinPosition", w.POINT), ("ptMaxPosition", w.POINT),
                        ("rcNormalPosition", w.RECT)]

        self.Placement = Placement
        specs = {
            "EnumWindows": ([self.enum_type, w.LPARAM], w.BOOL),
            "IsWindow": ([w.HWND], w.BOOL),
            "IsWindowVisible": ([w.HWND], w.BOOL),
            "IsIconic": ([w.HWND], w.BOOL),
            "GetWindowThreadProcessId": ([w.HWND, c.POINTER(w.DWORD)], w.DWORD),
            "GetClassNameW": ([w.HWND, w.LPWSTR, c.c_int], c.c_int),
            "GetWindowTextW": ([w.HWND, w.LPWSTR, c.c_int], c.c_int),
            "GetWindowPlacement": ([w.HWND, c.POINTER(Placement)], w.BOOL),
            "SetWindowPlacement": ([w.HWND, c.POINTER(Placement)], w.BOOL),
            "GetClientRect": ([w.HWND, c.POINTER(w.RECT)], w.BOOL),
            "ClientToScreen": ([w.HWND, c.POINTER(w.POINT)], w.BOOL),
            "SetWindowPos": ([w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT], w.BOOL),
            "SetWinEventHook": ([w.DWORD, w.DWORD, w.HMODULE, self.hook_type,
                                  w.DWORD, w.DWORD, w.DWORD], w.HANDLE),
            "UnhookWinEvent": ([w.HANDLE], w.BOOL),
            "GetMessageW": ([c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT], c.c_int),
            "PeekMessageW": ([c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT], w.BOOL),
            "PostThreadMessageW": ([w.DWORD, w.UINT, w.WPARAM, w.LPARAM], w.BOOL),
            "SetTimer": ([w.HWND, c.c_size_t, w.UINT, c.c_void_p], c.c_size_t),
            "KillTimer": ([w.HWND, c.c_size_t], w.BOOL),
            "TranslateMessage": ([c.POINTER(w.MSG)], w.BOOL),
            "DispatchMessageW": ([c.POINTER(w.MSG)], c.c_ssize_t),
        }
        for name, (args, result) in specs.items():
            fn = getattr(self.u, name)
            fn.argtypes, fn.restype = args, result
        self.k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.k.OpenProcess.restype = w.HANDLE
        self.k.CloseHandle.argtypes, self.k.CloseHandle.restype = [w.HANDLE], w.BOOL
        self.k.QueryFullProcessImageNameW.argtypes = [
            w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD),
        ]
        self.k.QueryFullProcessImageNameW.restype = w.BOOL
        self.k.GetCurrentThreadId.argtypes, self.k.GetCurrentThreadId.restype = [], w.DWORD

    def _pid(self, hwnd: int) -> int:
        pid = self.w.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, self.c.byref(pid))
        return pid.value

    def _executable(self, pid: int) -> Path | None:
        handle = self.k.OpenProcess(0x1000, False, pid)
        if not handle:
            return None  # A protected/exited process is not a selectable browser.
        try:
            size = self.w.DWORD(32768)
            buf = self.c.create_unicode_buffer(size.value)
            if not self.k.QueryFullProcessImageNameW(handle, 0, buf, self.c.byref(size)):
                return None
            return Path(buf.value)
        finally:
            self.k.CloseHandle(handle)

    def matches(self, window: BrowserWindow, executable: Path | None) -> bool:
        return bool(executable and window.handle != self.host
                    and self.u.IsWindow(window.handle)
                    and self._pid(window.handle) == window.pid
                    and self._executable(window.pid) == executable)

    def windows(self, executable: Path) -> list[BrowserWindow]:
        result: list[BrowserWindow] = []

        @self.enum_type
        def collect(hwnd: int, _data: int) -> bool:
            if not self.u.IsWindowVisible(hwnd) or self.u.IsIconic(hwnd):
                return True
            cls = self.c.create_unicode_buffer(256)
            self.u.GetClassNameW(hwnd, cls, len(cls))
            if cls.value != "Chrome_WidgetWin_1":
                return True
            pid = self._pid(hwnd)
            if self._executable(pid) != executable:
                return True
            title = self.c.create_unicode_buffer(512)
            self.u.GetWindowTextW(hwnd, title, len(title))
            result.append(BrowserWindow(int(hwnd), pid, title.value or "Chrome"))
            return True

        self.u.EnumWindows(collect, 0)
        return result

    def snapshot(self, window: BrowserWindow) -> Any:
        with per_monitor_dpi_context():
            placement = self.Placement(length=self.c.sizeof(self.Placement))
            if not self.u.GetWindowPlacement(window.handle, self.c.byref(placement)):
                raise self.c.WinError(self.c.get_last_error())
            normal = self.Placement.from_buffer_copy(placement)
            normal.showCmd = 4  # SW_SHOWNOACTIVATE: leave maximized state without stealing focus.
            if not self.u.SetWindowPlacement(window.handle, self.c.byref(normal)):
                raise self.c.WinError(self.c.get_last_error())
            return placement

    def restore(self, window: BrowserWindow, placement: Any) -> None:
        with per_monitor_dpi_context():
            if not self.u.SetWindowPlacement(window.handle, self.c.byref(placement)):
                log.warning("Could not restore the selected browser's window placement")

    def place(self, window: BrowserWindow, bounds: Viewport, *, raise_window: bool) -> None:
        with per_monitor_dpi_context():
            rect, origin = self.w.RECT(), self.w.POINT()
            if (not self.u.GetClientRect(self.host, self.c.byref(rect))
                    or not self.u.ClientToScreen(self.host, self.c.byref(origin))):
                raise RuntimeError("Host window is unavailable")
            x, y, width, height = bounds.pixels((origin.x, origin.y), (rect.right, rect.bottom))
            if width < 500 or height < 300:
                raise ValueError("Browser area is too small")
            flags = 0x0010 | 0x4000  # SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
            if not raise_window:
                flags |= 0x0004  # SWP_NOZORDER
            if not self.u.SetWindowPos(window.handle, 0, x, y, width, height, flags):
                raise self.c.WinError(self.c.get_last_error())

    def watch(self, callback: Any) -> Any:
        return _Watch(self, callback)


class _Watch:
    """WinEvent notifications plus a lease timer; no window-discovery polling."""

    def __init__(self, api: BrowserDock, callback: Any) -> None:
        self.api, self.callback = api, callback
        self.stopped, self.ready = threading.Event(), threading.Event()
        self.pending: queue.SimpleQueue[str] = queue.SimpleQueue()
        self.thread_id = 0
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, name="system-browser-dock", daemon=True)
        self.thread.start()
        if not self.ready.wait(2):
            self.stop()
            raise RuntimeError("Browser window observer did not start")
        if self.error:
            self.stop()
            raise self.error

    @property
    def alive(self) -> bool:
        return self.thread.is_alive()

    def stop(self) -> None:
        self.stopped.set()
        if self.thread_id:
            self.api.u.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                log.warning("Browser window observer did not stop before its deadline")

    def _run(self) -> None:
        api, hooks, timer = self.api, [], 0
        try:
            msg = api.w.MSG()
            api.u.PeekMessageW(api.c.byref(msg), None, 0, 0, 0)
            self.thread_id = api.k.GetCurrentThreadId()

            @api.hook_type
            def on_event(_hook: Any, event: int, hwnd: int, obj: int,
                         child: int, _thread: int, _time: int) -> None:
                if hwnd != api.host or obj != 0 or child != 0:
                    return
                kind = "host_focused" if event == 3 else "host_moved"
                self.pending.put(kind)
                api.u.PostThreadMessageW(self.thread_id, 0x8001, 0, 0)

            # Pin the callback for exactly the lifetime of its hooks.
            self.hook_callback = on_event
            for event in (3, 0x800B):  # FOREGROUND / OBJECT_LOCATIONCHANGE
                hook = api.u.SetWinEventHook(event, event, None, on_event, 0, 0, 0)
                if not hook:
                    raise api.c.WinError(api.c.get_last_error())
                hooks.append(hook)
            timer = api.u.SetTimer(None, 0, 1000, None)
            if not timer:
                raise api.c.WinError(api.c.get_last_error())
            self.ready.set()
            while not self.stopped.is_set():
                result = api.u.GetMessageW(api.c.byref(msg), None, 0, 0)
                if result <= 0:
                    if result < 0:
                        log.warning("Browser window observer message loop failed")
                    break
                if msg.message == 0x0113:  # WM_TIMER: recover on host/renderer disappearance.
                    gone = (not api.u.IsWindow(api.host) or not api.u.IsWindowVisible(api.host)
                            or api.u.IsIconic(api.host))
                    self.callback("host_gone" if gone else "lease")
                elif msg.message == 0x8001:
                    kinds: set[str] = set()
                    while not self.pending.empty():
                        kinds.add(self.pending.get_nowait())
                    if kinds:
                        self.callback("host_focused" if "host_focused" in kinds else "host_moved")
                else:
                    api.u.TranslateMessage(api.c.byref(msg))
                    api.u.DispatchMessageW(api.c.byref(msg))
        except Exception as exc:
            self.error = exc
            log.exception("Browser window observer failed")
        finally:
            for hook in hooks:
                api.u.UnhookWinEvent(hook)
            if timer:
                api.u.KillTimer(None, timer)
            if not self.stopped.is_set() and timer:
                # A failed message pump must not leave an apparently live dock.
                self.callback("host_gone")
            self.ready.set()
