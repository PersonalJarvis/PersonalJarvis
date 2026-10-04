"""Put a PNG image on the local desktop clipboard (Windows, macOS, Linux).

The embedded WebView cannot be trusted with ``navigator.clipboard.write`` for
images: WebView2 may never settle the promise, and WKWebView rejects it once a
click handler has awaited anything. So the desktop backend copies natively:

- **Windows** — ``CF_DIB`` (every app pastes it) plus the registered ``PNG``
  format (keeps transparency for apps that prefer it), through ctypes.
- **macOS** — ``osascript`` reading the PNG from a temporary file as
  ``«class PNGf»``; no extra package.
- **Linux** — ``wl-copy`` on Wayland, else ``xclip``; without either the copy
  reports that it cannot happen.

``write_png`` never raises and logs why a copy did not happen. A headless host
returns ``False`` without touching any OS integration.
"""

from __future__ import annotations

import ctypes
import io
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.platform import detect_platform
from jarvis.platform.capabilities import detect_capabilities

log = logging.getLogger(__name__)

_COMMAND_TIMEOUT_S = 5.0
_WINDOWS_CLIPBOARD_RETRIES = 10
_WINDOWS_CLIPBOARD_RETRY_S = 0.02
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: Win32 clipboard format for a device-independent bitmap.
_CF_DIB = 8
_GMEM_MOVEABLE = 0x0002


def is_png(data: bytes) -> bool:
    return data.startswith(_PNG_SIGNATURE)


def write_png(png: bytes) -> bool:
    """Replace the clipboard with this PNG. ``True`` only when the OS took it."""
    if not is_png(png):
        log.warning("clipboard-image: refusing a payload that is not a PNG")
        return False
    if not detect_capabilities().display_present:
        log.info("clipboard-image: no display present; native copy is unavailable")
        return False
    platform = detect_platform()
    if platform == "win32":
        return _write_windows(png)
    if platform == "darwin":
        return _write_macos(png)
    return _write_linux(png)


def png_to_dib(png: bytes) -> bytes:
    """The PNG as a ``CF_DIB`` payload: a BMP file without its 14-byte header."""
    from PIL import Image  # noqa: PLC0415 - Pillow only when an image is copied

    with Image.open(io.BytesIO(png)) as image:
        rgb = image.convert("RGB")
        out = io.BytesIO()
        rgb.save(out, format="BMP")
    return out.getvalue()[14:]


def _run(command: list[str], data: bytes | None = None) -> bool:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed, non-shell OS command
            command,
            input=data,
            capture_output=True,
            timeout=_COMMAND_TIMEOUT_S,
            check=False,
            close_fds=True,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("clipboard-image: copy command unavailable (%s)", exc)
        return False
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        log.warning(
            "clipboard-image: copy command failed with exit %d%s",
            completed.returncode,
            f" ({detail[:300]})" if detail else "",
        )
        return False
    return True


def _write_linux(png: bytes) -> bool:
    wl_copy = shutil.which("wl-copy")
    if wl_copy and os.environ.get("WAYLAND_DISPLAY"):
        return _run([wl_copy, "--type", "image/png"], png)
    xclip = shutil.which("xclip")
    if xclip:
        return _run([xclip, "-selection", "clipboard", "-t", "image/png", "-i"], png)
    if wl_copy:
        return _run([wl_copy, "--type", "image/png"], png)
    log.info("clipboard-image: neither wl-copy nor xclip is installed")
    return False


def _write_macos(png: bytes) -> bool:
    handle, name = tempfile.mkstemp(prefix="jarvis-appshot-", suffix=".png")
    path = Path(name)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(png)
        # The path travels as an argument to AppleScript's `item 1 of argv`,
        # never spliced into the script source.
        script = (
            "on run argv\n"
            "set the clipboard to (read (POSIX file (item 1 of argv)) as «class PNGf»)\n"
            "end run"
        )
        return _run(["/usr/bin/osascript", "-e", script, str(path)])
    except OSError as exc:
        log.warning("clipboard-image: could not stage the PNG for osascript (%s)", exc)
        return False
    finally:
        path.unlink(missing_ok=True)


def _write_windows(png: bytes) -> bool:
    try:
        dib = png_to_dib(png)
    except Exception as exc:  # noqa: BLE001 - a picture Pillow cannot read is not copyable
        log.warning("clipboard-image: could not convert the PNG to a bitmap (%s)", exc)
        return False
    try:
        from ctypes import wintypes  # noqa: PLC0415

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterClipboardFormatW.restype = wintypes.UINT
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    except (AttributeError, OSError) as exc:
        log.warning("clipboard-image: Win32 API unavailable (%s)", exc)
        return False

    def put(fmt: int, data: bytes) -> bool:
        handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(fmt, handle):
            # Ownership only passes to the clipboard on success.
            kernel32.GlobalFree(handle)
            return False
        return True

    opened = False
    for _attempt in range(_WINDOWS_CLIPBOARD_RETRIES):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(_WINDOWS_CLIPBOARD_RETRY_S)
    if not opened:
        log.warning("clipboard-image: another application holds the clipboard open")
        return False
    try:
        if not user32.EmptyClipboard():
            log.warning("clipboard-image: EmptyClipboard failed")
            return False
        if not put(_CF_DIB, dib):
            log.warning("clipboard-image: SetClipboardData(CF_DIB) failed")
            return False
        png_format = user32.RegisterClipboardFormatW("PNG")
        if png_format and not put(png_format, png):
            # The bitmap is already there and pastes everywhere; the PNG
            # format only adds transparency for apps that read it.
            log.info("clipboard-image: the extra PNG format was not accepted")
        return True
    finally:
        user32.CloseClipboard()


__all__ = ["is_png", "png_to_dib", "write_png"]
