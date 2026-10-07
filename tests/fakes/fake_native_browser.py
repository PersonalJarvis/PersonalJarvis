"""Owned Win32 windows and captured pixels without a desktop or native DLLs."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from types import SimpleNamespace


class NativeCall:
    def __init__(self, action):
        self.action = action

    def __call__(self, *args):
        return self.action(*args)


class WindowEvents(asyncio.Queue):
    """Synchronous fixtures make the owning-loop invalidation immediate."""

    def __init__(self, dirty):
        super().__init__()
        self.dirty = dirty

    def put_nowait(self, item):
        super().put_nowait(item)
        self.dirty.set()


class Pixels:
    def __init__(self, width, height, value=0):
        self.shape = (height, width, 4)
        self.rows = [[value] * width for _ in range(height)]

    def copy(self):
        copy = Pixels(self.shape[1], self.shape[0])
        copy.rows = [row[:] for row in self.rows]
        return copy

    def __setitem__(self, area, pixels):
        ys, xs = area
        for iy, y in enumerate(range(ys.start, ys.stop)):
            self.rows[y][xs] = pixels.rows[iy]


class ImageCodec:
    IMWRITE_JPEG_QUALITY = 1

    def __init__(self):
        self.canvas = None

    def imencode(self, suffix, canvas, options):
        self.canvas = canvas
        return True, SimpleNamespace(tobytes=lambda: b"owned-window-image")

    def resize(self, pixels, size):
        return Pixels(*size, pixels.rows[0][0])


class CaptureControl:
    def __init__(self):
        self.stopped = self.waited = 0

    def stop(self):
        self.stopped += 1

    def wait(self):
        self.waited += 1


class BlockingStopControl(CaptureControl):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def stop(self):
        self.entered.set()
        self.release.wait(5)
        super().stop()


class Capture:
    def __init__(self, **options):
        self.options = options
        self.events = {}
        self.control = CaptureControl()

    def event(self, handler):
        self.events[handler.__name__] = handler
        return handler

    def start_free_threaded(self):
        return self.control

    def arrive(self, pixels):
        self.events["on_frame_arrived"](SimpleNamespace(frame_buffer=pixels), None)


class BlockingCaptureFactory:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.captures = []

    def __call__(self, **options):
        self.started.set()
        if not self.release.wait(2):
            raise RuntimeError("Test capture creation did not resume")
        capture = Capture(**options)
        self.captures.append(capture)
        return capture


class NativeDesktop:
    def __init__(self):
        self.windows = {
            1: dict(pid=42, root=1, owner=0, rect=(100, 200, 200, 280), visible=True),
        }
        self.order = [1]
        self.messages = []
        self.unhooks = []
        self.hooks = []
        self.hook_thread = 0
        self.hook_callback = None
        self.pump_stop = threading.Event()
        self.co_initialized = self.co_uninitialized = 0
        self.hit = 1
        self.positions = []
        self.children = {}
        self.focus = 0
        self.user32 = SimpleNamespace(
            GetWindowThreadProcessId=NativeCall(self.thread_process),
            GetAncestor=NativeCall(lambda hwnd, kind: self.windows.get(hwnd, {}).get("root", 0)),
            GetWindow=NativeCall(lambda hwnd, kind: self.windows.get(hwnd, {}).get("owner", 0)),
            GetClassNameW=NativeCall(self.class_name),
            IsWindowVisible=NativeCall(
                lambda hwnd: self.windows.get(hwnd, {}).get("visible", False)
            ),
            EnumWindows=NativeCall(self.enum_windows),
            ScreenToClient=NativeCall(lambda hwnd, point: self.translate(hwnd, point, -1)),
            ClientToScreen=NativeCall(lambda hwnd, point: self.translate(hwnd, point, 1)),
            ChildWindowFromPointEx=NativeCall(
                lambda hwnd, point, flags: self.children.get(hwnd, hwnd)
            ),
            PostMessageW=NativeCall(self.post),
            GetGUIThreadInfo=NativeCall(self.gui_info),
            SendMessageTimeoutW=NativeCall(self.send),
            SetWindowPos=NativeCall(self.position),
            MonitorFromWindow=NativeCall(lambda *args: 1),
            GetMonitorInfoW=NativeCall(self.monitor_info),
            PeekMessageW=NativeCall(lambda *args: 1),
            SetWinEventHook=NativeCall(self.hook),
            UnhookWinEvent=NativeCall(self.unhook),
            PostThreadMessageW=NativeCall(self.quit),
        )

    def add(self, hwnd, *, owner=1, pid=42, rect=(170, 210, 230, 260), root=None):
        self.windows[hwnd] = dict(
            pid=pid, owner=owner, root=root or hwnd, rect=rect, visible=True
        )
        self.order.insert(0, hwnd)

    def class_name(self, hwnd, buffer, _length):
        buffer.value = self.windows.get(hwnd, {}).get("class_name", "Chrome_WidgetWin_1")
        return len(buffer.value)

    def thread_process(self, hwnd, pointer):
        if pointer is not None:
            pointer._obj.value = self.windows.get(hwnd, {}).get("pid", 0)
        return 77

    def enum_windows(self, callback, _unused):
        for hwnd in self.order:
            callback(hwnd, 0)
        return True

    def translate(self, hwnd, pointer, direction):
        rect = self.windows[hwnd]["rect"]
        pointer._obj.x += rect[0] * direction
        pointer._obj.y += rect[1] * direction
        return True

    def post(self, hwnd, message, key, value):
        self.messages.append((hwnd, message, key, value))
        return True

    def send(self, hwnd, message, key, value, flags, timeout, result):
        result._obj.value = self.hit
        return True

    def position(self, *args):
        self.positions.append(args)
        return True

    def gui_info(self, _thread, pointer):
        pointer._obj.hwndFocus = self.focus
        return True

    def monitor_info(self, _monitor, pointer):
        pointer._obj.rcWork.left, pointer._obj.rcWork.top = 0, 0
        pointer._obj.rcWork.right, pointer._obj.rcWork.bottom = 1920, 1080
        return True

    def hook(self, first, last, _module, callback, pid, thread, flags):
        self.hook_thread = threading.get_ident()
        handle = len(self.hooks) + 90
        self.hooks.append((handle, first, last, pid))
        self.hook_callback = callback
        return handle

    def unhook(self, handle):
        assert threading.get_ident() == self.hook_thread
        self.unhooks.append(handle)
        return True

    def quit(self, thread, message, key, value):
        self.pump_stop.set()
        return True

    def com_initialize(self):
        self.co_initialized += 1

    def com_uninitialize(self):
        self.co_uninitialized += 1

    def instance(self):
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        from jarvis.society.browser.native_window import NativeWindow

        native = NativeWindow.__new__(NativeWindow)
        kernel = SimpleNamespace(GetCurrentThreadId=NativeCall(lambda: 77))
        native.ctypes = SimpleNamespace(
            **{name: getattr(ctypes, name) for name in (
                "byref", "sizeof", "POINTER", "Structure", "c_int", "c_long", "c_size_t",
                "c_void_p", "create_unicode_buffer"
            )},
            WinDLL=lambda name: kernel,
            WINFUNCTYPE=lambda *args: lambda callback: callback,
        )
        native.wintypes = wintypes
        native.user32 = self.user32
        native.callback_type = lambda callback: callback
        native.pid, native.hwnd, native.input_hwnd = 42, 1, 1
        native.failed = native._closed = False
        native.lock, native.input_lock = threading.RLock(), threading.RLock()
        native.ready = threading.Event()
        native.latest = None
        native._capture_factory = Capture
        native._captures, native._images = {}, {}
        native._capture_keys = {}
        native._popups, native._regions, native._hooks = [], [], []
        native._revision, native._encoded_revision, native._geometry_id = 0, -1, 0
        native._windows_dirty = threading.Event()
        native._events = WindowEvents(native._windows_dirty)
        native._event_latch = threading.Lock()
        native._event_task = None
        native._loop = SimpleNamespace(call_soon_threadsafe=lambda fn, *args: fn(*args))
        native._hook_ready = threading.Event()
        native._hook_thread_id, native._hook_error = 0, None
        native._hook_thread = SimpleNamespace(join=lambda **kw: None, is_alive=lambda: False)
        native.dpi = contextlib.nullcontext
        native._bounds = lambda hwnd: self.windows[hwnd]["rect"]
        native._start_capture(1)
        native._captures[1][0].arrive(Pixels(100, 80, 1))
        return native
