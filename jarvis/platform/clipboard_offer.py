"""Observe real Windows clipboard rendering without treating silence as failure.

A message-only window owns a delayed Unicode text offer. The first successful
render records the reader PID; cached reads produce no further events. Clipboard
watchers can consume that first render, so no event is never proof that a paste
failed, and merely opening the clipboard is never insertion evidence.

The dedicated thread waits on native messages. Stopping renders the transcript
for later manual paste. A caller with delivery evidence may request restoration;
ownership is checked under the same clipboard lock as the replacement, preserving
any newer user copy. All native imports are lazy and this helper is inert off Windows.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
_WM_DESTROY = 0x0002
_WM_RENDERFORMAT = 0x0305
_WM_RENDERALLFORMATS = 0x0306
_WM_DESTROYCLIPBOARD = 0x0307
_WM_APP_STOP = 0x8000 + 41
_HWND_MESSAGE = -3


@dataclass(frozen=True, slots=True)
class ClipboardRead:
    """One process pulling the offered text off the clipboard."""

    pid: int
    exe: str
    #: Seconds since the offer went up.
    at: float
    #: ``render`` — the system asked us for the text (a real ``GetClipboardData``);
    #: Historical consumers may supply ``open``; it is not delivery evidence.
    observed: str = "render"


def available() -> bool:
    """Can a delayed-rendering offer be made on this host?"""
    return os.name == "nt" and sys.platform == "win32"


class ClipboardOffer:
    """Own the clipboard with delayed rendering and record every read.

    Usage::

        offer = ClipboardOffer(text)
        if offer.start():
            ... send the paste chord ...
            read = offer.wait_for_read(exclude_pids={...}, timeout_s=0.6)
            offer.stop()   # renders the text for real, keeps it on the clipboard

    Never raises past its public methods: a failure to take the clipboard is a
    ``False`` from :meth:`start` and the caller uses the plain write path.
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self._reads: list[ClipboardRead] = []
        self._lock = threading.Lock()
        self._read_event = threading.Event()
        self._ready = threading.Event()
        self._done = threading.Event()
        self._stop_requested = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._hwnd: int = 0
        self._started_at = 0.0
        self._ok = False
        self.lost_ownership = False
        #: True once the text was handed to a reader. From then on the system
        #: caches it and answers later readers itself — no more render events.
        self.rendered = False
        self.restored = False
        self._restore_text: str | None = None
        self._wndproc_ref: object = None  # keeps the ctypes callback alive

    # -- public ---------------------------------------------------------

    def start(self, *, timeout_s: float = 1.0) -> bool:
        """Take the clipboard. ``True`` when the offer is up."""
        if not available():
            return False
        with self._lifecycle_lock:
            if self._stop_requested.is_set():
                return False
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="jarvis-clipboard-offer", daemon=True
                )
                self._thread.start()
        if not self._ready.wait(timeout_s):
            # A late worker must never take the clipboard after our caller
            # already chose its fallback delivery route.
            self.stop()
            return False
        return self._ok and not self._done.is_set()

    def reads(self) -> list[ClipboardRead]:
        with self._lock:
            return list(self._reads)

    def wait_for_read(
        self,
        *,
        exclude_pids: set[int],
        after_s: float,
        timeout_s: float,
    ) -> ClipboardRead | None:
        """First read after *after_s* (offer-relative) by a pid not excluded."""
        deadline = time.monotonic() + timeout_s
        while True:
            with self._lock:
                for read in self._reads:
                    if read.at >= after_s and read.pid not in exclude_pids:
                        return read
                self._read_event.clear()
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._done.is_set() or self.lost_ownership:
                return None
            self._read_event.wait(remaining)

    def elapsed(self) -> float:
        """Seconds since the offer went up."""
        return time.monotonic() - self._started_at if self._started_at else 0.0

    def stop(self, *, timeout_s: float = 2.0, restore_text: str | None = None) -> None:
        """Finish the offer, optionally restoring text only while we still own it."""
        self._restore_text = restore_text
        self._stop_requested.set()
        thread = self._thread
        if thread is None:
            return
        if self._hwnd:
            try:
                import ctypes  # noqa: PLC0415
                from ctypes import wintypes  # noqa: PLC0415

                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.PostMessageW.argtypes = [
                    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                ]
                user32.PostMessageW.restype = wintypes.BOOL
                if not user32.PostMessageW(self._hwnd, _WM_APP_STOP, 0, 0):
                    log.debug("clipboard offer stop message was refused")
            except Exception:  # noqa: BLE001 — the thread's own timeout covers it
                log.debug("could not post stop to the clipboard offer window", exc_info=True)
        thread.join(timeout_s)
        if not thread.is_alive():
            self._thread = None

    # -- thread body ----------------------------------------------------

    def _restore_owned_text(self, user32, hwnd: int, render) -> None:
        """Check ownership and replace text under one native clipboard lock."""
        if self._restore_text is None or not user32.OpenClipboard(hwnd):
            return
        try:
            if user32.GetClipboardOwner() == hwnd and user32.EmptyClipboard():
                self.restored = render(self._restore_text)
                if self.restored:
                    self._text = self._restore_text
                else:
                    render()
        finally:
            user32.CloseClipboard()

    def _run(self) -> None:
        try:
            self._pump()
        except Exception:  # noqa: BLE001 — the offer simply reports "not up"
            log.debug("clipboard offer thread failed", exc_info=True)
        finally:
            self._ok = self._ok and self._ready.is_set()
            self._ready.set()
            self._done.set()
            self._read_event.set()

    def _pump(self) -> None:
        if not available() or self._stop_requested.is_set():
            return
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        wndproc_t = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.UnregisterClassW.restype = wintypes.BOOL
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
            wintypes.UINT,
        ]
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.GetClipboardOwner.restype = wintypes.HWND
        user32.GetOpenClipboardWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def render(value: str | None = None) -> bool:
            """Hand the real text to whoever asked. Clipboard must be open."""
            buf = ctypes.create_unicode_buffer(self._text if value is None else value)
            size = ctypes.sizeof(buf)
            handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, size)
            if not handle:
                return False
            target = kernel32.GlobalLock(handle)
            if not target:
                kernel32.GlobalFree(handle)
                return False
            try:
                ctypes.memmove(target, ctypes.addressof(buf), size)
            finally:
                kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(_CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)
                return False
            return True

        def reader_pid() -> int:
            reader = user32.GetOpenClipboardWindow()
            if not reader:
                return 0
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(reader, ctypes.byref(pid))
            return int(pid.value)

        def wndproc(hwnd, msg, wparam, lparam):  # noqa: ANN001 — Win32 callback
            try:
                if msg == _WM_RENDERFORMAT:
                    pid = reader_pid()
                    if int(wparam) == _CF_UNICODETEXT and render():
                        self.rendered = True
                        # Publish only successfully rendered data, and keep
                        # filesystem/process queries off the target's read.
                        read = ClipboardRead(pid=pid, exe="", at=self.elapsed())
                        with self._lock:
                            self._reads.append(read)
                        self._read_event.set()
                    return 0
                if msg == _WM_RENDERALLFORMATS:
                    # We are about to stop owning the clipboard: leave the text
                    # behind for real, per the documented contract.
                    if user32.OpenClipboard(hwnd):
                        try:
                            if user32.GetClipboardOwner() == hwnd:
                                render()
                        finally:
                            user32.CloseClipboard()
                    return 0
                if msg == _WM_DESTROYCLIPBOARD:
                    self.lost_ownership = True
                    self._read_event.set()
                    return 0
                if msg == _WM_APP_STOP:
                    self._stop_requested.set()
                    return 0
                if msg == _WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
            except Exception:  # noqa: BLE001 — a callback must never unwind into Win32
                log.debug("clipboard offer wndproc failed", exc_info=True)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc_ref = wndproc_t(wndproc)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", wndproc_t),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class_name = f"JarvisClipboardOffer-{threading.get_ident()}"
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_ref
        wc.lpszClassName = class_name
        wc.hInstance = kernel32.GetModuleHandleW(None)
        if not user32.RegisterClassW(ctypes.byref(wc)):
            log.debug("RegisterClassW failed: %s", ctypes.get_last_error())
            return
        hwnd = user32.CreateWindowExW(
            0, class_name, "offer", 0, 0, 0, 0, 0, _HWND_MESSAGE, None, wc.hInstance, None
        )
        if not hwnd:
            log.debug("CreateWindowExW failed: %s", ctypes.get_last_error())
            user32.UnregisterClassW(class_name, wc.hInstance)
            return
        self._hwnd = int(hwnd)

        taken = False
        for _attempt in range(10):
            if self._stop_requested.is_set():
                break
            if user32.OpenClipboard(hwnd):
                try:
                    user32.EmptyClipboard()
                    # NULL handle = delayed rendering: the text is produced on
                    # demand, and every demand becomes a recorded read. The
                    # return value is the handle we passed (NULL), so success
                    # is read from the error state, not the result.
                    ctypes.set_last_error(0)
                    user32.SetClipboardData(_CF_UNICODETEXT, None)
                    taken = ctypes.get_last_error() == 0
                finally:
                    user32.CloseClipboard()
                break
            time.sleep(0.01)
        if not taken:
            user32.DestroyWindow(hwnd)
            user32.UnregisterClassW(class_name, wc.hInstance)
            return

        self._started_at = time.monotonic()
        self._ok = True
        self._ready.set()

        # Clipboard opening is not a read or an insertion acknowledgment.
        # Wait for native messages; never sample the clipboard every millisecond.
        msg = wintypes.MSG()
        user32.MsgWaitForMultipleObjectsEx.argtypes = [
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        running = True
        try:
            while running and not self._stop_requested.is_set():
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    if msg.message == 0x0012:  # WM_QUIT
                        running = False
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                if not running or self._stop_requested.is_set():
                    break
                # QS_ALLINPUT = 0x04FF, MWMO_INPUTAVAILABLE = 0x0004
                user32.MsgWaitForMultipleObjectsEx(0, None, 1000, 0x04FF, 0x0004)
        finally:
            # A newer copy of identical text still belongs to its new owner.
            try:
                self._restore_owned_text(user32, hwnd, render)
            finally:
                if user32.IsWindow(hwnd):
                    user32.DestroyWindow(hwnd)
                user32.UnregisterClassW(class_name, wc.hInstance)
                self._hwnd = 0


__all__ = ["ClipboardOffer", "ClipboardRead", "available"]
