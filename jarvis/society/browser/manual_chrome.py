"""Plain installed Chrome for a person's in-app login, with no debug transport.

This sibling runs in the isolated browser worker, so it imports no Jarvis code.
The parent places that worker in a kill-on-close process job before sending
commands. Chrome inherits that containment; it never receives breakaway flags.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
_PIN = ".jarvis-chrome.json"


def _candidates() -> list[Path]:
    return [
        Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
        for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
        if (root := os.environ.get(name))
    ]


def find_installed_chrome() -> str | None:
    """Only stable Google Chrome; Chromium/CfT cannot replace a Chrome identity."""
    if os.name != "nt":
        return None
    return next((str(path.resolve()) for path in _candidates() if path.is_file()), None)


def _file_version(executable: Path) -> tuple[int, ...]:
    if os.name != "nt":
        return ()
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    library = ctypes.WinDLL("version", use_last_error=True)
    library.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    library.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    library.GetFileVersionInfoW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p
    ]
    library.GetFileVersionInfoW.restype = wintypes.BOOL
    library.VerQueryValueW.argtypes = [
        ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    library.VerQueryValueW.restype = wintypes.BOOL
    size = library.GetFileVersionInfoSizeW(str(executable), None)
    if not size or size > 4 * 1024 * 1024:
        raise RuntimeError("The installed Chrome version could not be read")
    data = ctypes.create_string_buffer(size)
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if (
        not library.GetFileVersionInfoW(str(executable), 0, size, data)
        or not library.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length))
        or length.value < 52
    ):
        raise RuntimeError("The installed Chrome version could not be read")
    values = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    if values[0] != 0xFEEF04BD:
        raise RuntimeError("The installed Chrome version is invalid")
    return (values[2] >> 16, values[2] & 0xFFFF, values[3] >> 16, values[3] & 0xFFFF)


def _validate_profile(profile: Path) -> None:
    local = os.environ.get("LOCALAPPDATA")
    if local and profile.resolve().is_relative_to(
        (Path(local) / "Google" / "Chrome" / "User Data").resolve()
    ):
        raise RuntimeError("In-app login requires a Jarvis-owned browser profile")


def _validate_executable(profile: Path, executable: Path) -> str:
    _validate_profile(profile)
    if (
        executable.resolve() not in {path.resolve() for path in _candidates()}
        or not executable.is_file()
    ):
        raise RuntimeError("This profile's Chrome is unavailable; reinstall Google Chrome")
    installed = _file_version(executable)
    if not installed:
        raise RuntimeError("The installed Chrome version could not be read")
    last = profile / "Last Version"
    if last.exists():
        try:
            raw = last.read_text(encoding="utf-8").strip().split(".")
            if len(raw) != 4 or any(not part.isdecimal() for part in raw):
                raise ValueError("Invalid Chrome profile version")
            used = tuple(int(part) for part in raw)
        except (OSError, ValueError) as exc:
            raise RuntimeError("The browser profile version could not be verified") from exc
        if installed < used:
            raise RuntimeError(
                "Update Google Chrome before signing in: this profile was opened by a newer browser"
            )
    return str(executable.resolve())


def pinned_chrome_executable(profile: Path) -> str | None:
    """Missing pins allow the legacy runtime; invalid pins never do."""
    if os.name != "nt":
        return None
    marker = profile / _PIN
    if not marker.exists():
        return None
    try:
        row = json.loads(marker.read_text(encoding="utf-8"))
        executable = row["executable"]
        if row.get("version") != 1 or not isinstance(executable, str) or not executable:
            raise ValueError("Invalid Chrome profile identity")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("The browser profile's Chrome identity could not be verified") from exc
    return _validate_executable(profile, Path(executable))


def select_chrome_executable(profile: Path) -> str | None:
    """Pin stable Chrome once, retaining the same binary identity for agent use."""
    if os.name != "nt":
        return None
    existing = pinned_chrome_executable(profile)
    if existing:
        return existing
    executable = find_installed_chrome()
    if not executable:
        raise RuntimeError("Install Google Chrome to sign in inside Jarvis")
    selected = _validate_executable(profile, Path(executable))
    profile.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=profile, prefix=".jarvis-chrome-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump({"version": 1, "executable": selected}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, profile / _PIN)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return selected


class _WindowArrival:
    """Bounded, PID-scoped window discovery without polling the desktop."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.loop = asyncio.get_running_loop()
        self.events: asyncio.Queue[int] = asyncio.Queue()
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.thread_id = 0
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, name="jarvis-chrome-start", daemon=True)

    def _run(self) -> None:
        if os.name != "nt":
            self.ready.set()
            return
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        hook = None
        initialized = False
        try:
            import pythoncom  # noqa: PLC0415

            pythoncom.CoInitialize()
            initialized = True
            user = ctypes.WinDLL("user32", use_last_error=True)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentThreadId.restype = wintypes.DWORD
            self.thread_id = kernel.GetCurrentThreadId()
            user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                         wintypes.UINT, wintypes.UINT, wintypes.UINT]
            user.PeekMessageW.restype = wintypes.BOOL
            message = wintypes.MSG()
            user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            callback_type = ctypes.WINFUNCTYPE(
                None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                ctypes.c_long, ctypes.c_long, wintypes.DWORD, wintypes.DWORD,
            )

            @callback_type
            def callback(_hook, _event, hwnd, object_id, child_id, _thread, _time):
                if hwnd and object_id == 0 and child_id == 0 and not self.stopping.is_set():
                    try:
                        self.loop.call_soon_threadsafe(self.events.put_nowait, int(hwnd))
                    except RuntimeError:
                        # Worker loop shutdown already owns listener cleanup.
                        return

            user.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
                                            callback_type, wintypes.DWORD, wintypes.DWORD,
                                            wintypes.DWORD]
            user.SetWinEventHook.restype = wintypes.HANDLE
            user.UnhookWinEvent.argtypes = [wintypes.HANDLE]
            user.UnhookWinEvent.restype = wintypes.BOOL
            # CREATE through SHOW covers Chrome's first visible top-level window.
            hook = user.SetWinEventHook(0x8000, 0x8002, None, callback, self.pid, 0, 0)
            if not hook:
                raise RuntimeError("Chrome window startup events are unavailable")
            self.ready.set()
            if not self.stopping.is_set():
                pythoncom.PumpMessages()
        except BaseException as exc:  # The stored exception is re-raised by the thread owner.
            self.error = exc
        finally:
            if hook and not user.UnhookWinEvent(hook):
                log.warning("Could not release Chrome startup window hook")
            self.ready.set()
            if initialized:
                pythoncom.CoUninitialize()

    def stop(self) -> None:
        self.stopping.set()
        if os.name == "nt" and self.thread_id:
            import ctypes  # noqa: PLC0415
            from ctypes import wintypes  # noqa: PLC0415

            user = ctypes.WinDLL("user32", use_last_error=True)
            user.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                              wintypes.WPARAM, wintypes.LPARAM]
            user.PostThreadMessageW.restype = wintypes.BOOL
            if not user.PostThreadMessageW(self.thread_id, 0x12, 0, 0):
                log.debug("Chrome startup message pump already stopped")
        if self.thread.ident is not None:
            self.thread.join(timeout=2.0)
            if self.thread.is_alive():
                raise RuntimeError("Chrome startup window listener did not stop")


def _owned_windows(pid: int) -> list[int]:
    if os.name != "nt":
        return []
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetClassNameW.restype = ctypes.c_int
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    windows: list[int] = []

    @callback_type
    def visit(hwnd, _value):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        name = ctypes.create_unicode_buffer(128)
        user.GetClassNameW(hwnd, name, len(name))
        if owner.value == pid and user.IsWindowVisible(hwnd) and name.value == "Chrome_WidgetWin_1":
            windows.append(int(hwnd))
        return True

    if not user.EnumWindows(visit, 0):
        raise RuntimeError("Chrome window ownership could not be checked")
    return windows


async def _wait_for_window(pid: int, *, timeout: float = 15.0) -> None:  # noqa: ASYNC109
    watcher = _WindowArrival(pid)
    watcher.thread.start()
    try:
        if not await asyncio.to_thread(watcher.ready.wait, 2.0):
            raise RuntimeError("Chrome startup window listener did not start")
        if watcher.error:
            raise RuntimeError("Chrome startup window listener failed") from watcher.error
        async with asyncio.timeout(timeout):
            while not await asyncio.to_thread(_owned_windows, pid):
                await watcher.events.get()
    finally:
        await asyncio.to_thread(watcher.stop)


def _request_close(pid: int) -> None:
    if os.name != "nt":
        return
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    user = ctypes.WinDLL("user32", use_last_error=True)
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    for hwnd in _owned_windows(pid):
        # A recycled HWND must not close another application.
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and not user.PostMessageW(hwnd, 0x10, 0, 0):
            raise RuntimeError("The owned Chrome window could not be closed")


def _make_native(pid: int) -> Any:
    from native_window import NativeWindow  # type: ignore[import-not-found]  # noqa: PLC0415

    return NativeWindow(pid)


class PlainChrome:
    """One visible owned Chrome process; timeout never permits profile reuse."""

    def __init__(self, profile: Path, executable: str, *, creationflags: int) -> None:
        self.profile = Path(profile)
        self.executable = executable
        self.creationflags = creationflags
        self.process: Any = None
        self.native: Any = None
        self.lock = asyncio.Lock()

    async def start(self) -> Any:
        if os.name != "nt":
            return None
        async with self.lock:
            if self.process is not None:
                raise RuntimeError("The login browser is already open; close it before reopening")
            _validate_executable(self.profile, Path(self.executable))
            # Keep spawn and ownership assignment atomic against cancellation.
            self.process = subprocess.Popen(  # noqa: ASYNC220
                [self.executable, f"--user-data-dir={self.profile.resolve()}", "--new-window",
                 "--no-first-run", "--disable-background-mode",
                 "--disable-backgrounding-occluded-windows", "chrome://newtab/"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=self.creationflags,
            )
            await _wait_for_window(self.process.pid)
            if self.process.poll() is not None:
                raise RuntimeError("Chrome could not acquire this browser profile")
            self.native = _make_native(self.process.pid)
            if not await asyncio.to_thread(self.native.ready.wait, 10):
                raise RuntimeError("Chrome did not produce a login window image")
            self.native.frame()
            await asyncio.to_thread(self.native.park)
            return self.native

    async def close(self, *, timeout: float = 8.0) -> None:  # noqa: ASYNC109
        async with self.lock:
            if self.process is None:
                return
            process = self.process
            if process.poll() is None:
                await asyncio.to_thread(_request_close, process.pid)
                try:
                    await asyncio.to_thread(process.wait, timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(
                        "Chrome is still closing. Finish open dialogs before returning to the agent"
                    ) from exc
            if self.native is not None:
                await asyncio.to_thread(self.native.close)
                self.native = None
            self.process = None
