"""Put a PNG image on the system clipboard (Windows, macOS, Linux).

The app's WebView cannot be trusted with ``navigator.clipboard`` for images,
so the backend does it natively:

- **Windows** — ``CF_DIB`` (every app pastes it) plus the registered ``PNG``
  format (keeps transparency for apps that prefer it), via ctypes.
- **macOS** — ``osascript`` with the PNG as ``«class PNGf»``; no extra
  package needed.
- **Linux** — ``wl-copy`` on Wayland, else ``xclip``; without either the copy
  degrades to a clear message.

Every function returns ``(ok, message)``, never raises, and logs why a copy
did not happen. Only stdlib at module scope; Pillow loads lazily.
"""

from __future__ import annotations

import ctypes
import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

_TIMEOUT_S = 5.0
_RETRIES = 10


def copy_png(png: bytes) -> tuple[bool, str]:
    """Replace the clipboard with this PNG image."""
    try:
        from jarvis.platform.probes import display_present  # noqa: PLC0415

        if not display_present():
            return False, "There is no desktop on this computer, so there is no clipboard."
    except Exception:  # noqa: BLE001 - probe missing: try the copy anyway
        log.debug("jarvisx: display probe failed", exc_info=True)
    if sys.platform == "win32":
        return _copy_windows(png)
    if sys.platform == "darwin":
        return _copy_macos(png)
    return _copy_linux(png)


def _run(command: list[str], data: bytes | None = None) -> tuple[bool, str]:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed OS clipboard command
            command,
            input=data,
            capture_output=True,
            timeout=_TIMEOUT_S,
            check=False,
            close_fds=True,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("jarvisx: clipboard command %s failed to run (%s)", command[0], exc)
        return False, "The clipboard tool could not be started."
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:300]
        log.warning(
            "jarvisx: clipboard command %s exited %d %s", command[0], completed.returncode, detail
        )
        return False, "The clipboard refused the image."
    return True, "Copied to the clipboard."


def _copy_linux(png: bytes) -> tuple[bool, str]:
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return _run([str(shutil.which("wl-copy")), "--type", "image/png"], png)
    xclip = shutil.which("xclip")
    if xclip:
        return _run([xclip, "-selection", "clipboard", "-t", "image/png", "-i"], png)
    log.info("jarvisx: no wl-copy or xclip; image copy unavailable")
    return False, "Copying images needs 'xclip' (X11) or 'wl-clipboard' (Wayland) installed."


def _copy_macos(png: bytes) -> tuple[bool, str]:
    handle, name = tempfile.mkstemp(prefix="jarvisx-", suffix=".png")
    path = Path(name)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(png)
        posix = str(path).replace("\\", "\\\\").replace('"', '\\"')
        script = f'set the clipboard to (read (POSIX file "{posix}") as «class PNGf»)'
        return _run(["/usr/bin/osascript", "-e", script])
    finally:
        path.unlink(missing_ok=True)


def _dib_bytes(png: bytes) -> bytes:
    """The PNG as a packed DIB (a BMP without its 14-byte file header)."""
    from PIL import Image  # noqa: PLC0415

    with Image.open(io.BytesIO(png)) as image:
        rgb = image.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, format="BMP")
    return buffer.getvalue()[14:]


def _copy_windows(png: bytes) -> tuple[bool, str]:
    try:
        dib = _dib_bytes(png)
    except Exception as exc:  # noqa: BLE001 - a broken image is reported, not raised
        log.warning("jarvisx: image could not be prepared for the clipboard", exc_info=True)
        return False, f"The image could not be read ({exc})."
    try:
        from ctypes import wintypes  # noqa: PLC0415

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterClipboardFormatW.restype = wintypes.UINT
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL
    except (AttributeError, OSError) as exc:
        log.warning("jarvisx: Win32 clipboard API unavailable (%s)", exc)
        return False, "The Windows clipboard is not reachable."

    def _put(fmt: int, data: bytes) -> bool:
        handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        if not handle:
            return False
        target = kernel32.GlobalLock(handle)
        if not target:
            kernel32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(target, data, len(data))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(fmt, handle):
            kernel32.GlobalFree(handle)
            return False
        return True  # the clipboard owns the memory now

    for _attempt in range(_RETRIES):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return False, "Another app is holding the clipboard; try again."
    try:
        if not user32.EmptyClipboard():
            return False, "The clipboard could not be cleared."
        if not _put(8, dib):  # CF_DIB
            return False, "The clipboard refused the image."
        png_format = user32.RegisterClipboardFormatW("PNG")
        if png_format:
            _put(int(png_format), png)
        return True, "Copied to the clipboard."
    finally:
        user32.CloseClipboard()


__all__ = ["copy_png"]
