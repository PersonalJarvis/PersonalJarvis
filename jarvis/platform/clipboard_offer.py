"""Offer text on the Windows clipboard and OBSERVE who reads it.

Why this exists
---------------
Jarvis does not paste; it asks the foreground application to paste by sending
a synthetic chord. Two things about that are unknowable from the outside:
whether the chord means "paste" in that application at all (an xterm.js
terminal left to itself swallows Ctrl+V as ``^V`` and cancels the browser's
paste), and WHEN the application reads the clipboard — a WebView that pastes
through an async IPC bridge can read hundreds of milliseconds later on a busy
machine, after a timer-based "restore the previous clipboard" has already put
the old content back (the 2026-08-24 BridgeMind report).

Windows offers one mechanism that turns both into a measurement: **delayed
rendering**. A clipboard owner may publish a format with a ``NULL`` handle;
the data is requested through ``WM_RENDERFORMAT`` only when some process
actually calls ``GetClipboardData``. Because the reader must hold the
clipboard open at that moment, ``GetOpenClipboardWindow`` names it. So
"did the paste land, and when?" becomes "which process read the clipboard
after the chord went out?" — a fact, not a guess.

The one limit, measured: the system renders ONCE. After the first reader the
text is cached and later readers are served silently. A host with a clipboard
watcher — a Remote Desktop client (``msrdc.exe`` reads within 5 ms of every
write) or the clipboard-history service — therefore consumes the render before
the chord is even sent, and from then on the offer is *blind*: a paste can no
longer be proven absent, only (by polling for who holds the clipboard open)
occasionally proven present. Callers must treat the two states differently;
:mod:`jarvis.dictation.insert` does.

Design
------
* One hidden message-only window on a dedicated thread owns the clipboard for
  the duration of the offer and answers ``WM_RENDERFORMAT`` with the text.
* Every render request is recorded with the reader's pid and executable name.
* On :meth:`ClipboardOffer.stop` the text is rendered for real before the
  window goes away (``WM_RENDERALLFORMATS`` contract), so the clipboard still
  holds the text afterwards — the "one Ctrl+V away" guarantee of
  :mod:`jarvis.dictation.insert` survives.
* Losing ownership (another app copied something) is recorded, never raised.

Windows only, imported lazily by its one caller. On any other OS
:func:`available` is ``False`` and nothing here is constructed.
"""

from __future__ import annotations

import logging
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
    #: ``open`` — the process merely held the clipboard open, seen by polling.
    #: Once the text has been rendered and cached, ``open`` is the only
    #: evidence left (see :meth:`ClipboardOffer.rendered`).
    observed: str = "render"


def available() -> bool:
    """Can a delayed-rendering offer be made on this host?"""
    return sys.platform == "win32"


def _exe_name(pid: int) -> str:
    """Executable name for *pid*, ``""`` when it cannot be read. Never raises."""
    if pid <= 0:
        return ""
    try:
        import ctypes  # noqa: PLC0415 — lazy (HN-7)
        from ctypes import wintypes  # noqa: PLC0415

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return ""
            return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 — a nameless reader is still a reader
        log.debug("could not resolve exe name for pid %s", pid, exc_info=True)
        return ""


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
        self._thread: threading.Thread | None = None
        self._hwnd: int = 0
        self._started_at = 0.0
        self._ok = False
        self.lost_ownership = False
        #: True once the text was handed to a reader. From then on the system
        #: caches it and answers later readers itself — no more render events.
        self.rendered = False
        self._wndproc_ref: object = None  # keeps the ctypes callback alive

    # -- public ---------------------------------------------------------

    def start(self, *, timeout_s: float = 1.0) -> bool:
        """Take the clipboard. ``True`` when the offer is up."""
        if not available():
            return False
        self._thread = threading.Thread(
            target=self._run, name="jarvis-clipboard-offer", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout_s)
        return self._ok

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
            if remaining <= 0 or self._done.is_set():
                return None
            self._read_event.wait(min(remaining, 0.05))

    def elapsed(self) -> float:
        """Seconds since the offer went up."""
        return time.monotonic() - self._started_at if self._started_at else 0.0

    def stop(self, *, timeout_s: float = 1.0) -> None:
        """Render the text for real and give the window up. Idempotent."""
        if self._thread is None:
            return
        if self._hwnd:
            try:
                import ctypes  # noqa: PLC0415

                ctypes.WinDLL("user32").PostMessageW(self._hwnd, _WM_APP_STOP, 0, 0)
            except Exception:  # noqa: BLE001 — the thread's own timeout covers it
                log.debug("could not post stop to the clipboard offer window", exc_info=True)
        self._thread.join(timeout_s)
        self._thread = None

    # -- thread body ----------------------------------------------------

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

        def render() -> bool:
            """Hand the real text to whoever asked. Clipboard must be open."""
            buf = ctypes.create_unicode_buffer(self._text)
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
                    read = ClipboardRead(pid=pid, exe=_exe_name(pid), at=self.elapsed())
                    with self._lock:
                        self._reads.append(read)
                    self._read_event.set()
                    if int(wparam) == _CF_UNICODETEXT and render():
                        self.rendered = True
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
                    return 0
                if msg == _WM_APP_STOP:
                    user32.DestroyWindow(hwnd)
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

        # A message pump that also POLLS: once the text is rendered the system
        # serves later readers from its cache and sends no more render events,
        # so the only trace a paste leaves is the reader briefly holding the
        # clipboard open. Sampling that every millisecond is cheap and catches
        # a WebView's multi-format read comfortably; a missed sample only
        # means "no positive evidence", never a wrong claim.
        msg = wintypes.MSG()
        user32.MsgWaitForMultipleObjectsEx.argtypes = [
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        seen_open: set[int] = set()
        running = True
        try:
            while running:
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    if msg.message == 0x0012:  # WM_QUIT
                        running = False
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                if not running:
                    break
                holder = user32.GetOpenClipboardWindow()
                if holder and holder != hwnd:
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(holder, ctypes.byref(pid))
                    if pid.value and pid.value not in seen_open:
                        seen_open.add(int(pid.value))
                        read = ClipboardRead(
                            pid=int(pid.value),
                            exe=_exe_name(int(pid.value)),
                            at=self.elapsed(),
                            observed="open",
                        )
                        with self._lock:
                            self._reads.append(read)
                        self._read_event.set()
                # QS_ALLINPUT = 0x04FF, MWMO_INPUTAVAILABLE = 0x0004
                user32.MsgWaitForMultipleObjectsEx(0, None, 1, 0x04FF, 0x0004)
        finally:
            user32.UnregisterClassW(class_name, wc.hInstance)
            self._hwnd = 0


__all__ = ["ClipboardOffer", "ClipboardRead", "available"]
