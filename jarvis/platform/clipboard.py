"""Cross-platform system clipboard access for the local desktop shell.

The Web UI normally uses the browser Clipboard API. Embedded WebViews can deny
that API even after a visible button click (notably WKWebView after an awaited
request), so the desktop backend needs a native, capability-gated fallback.
Reading is denied outright: the embedded browser withholds the ``clipboard-read``
permission, so a right-click "Paste" has no browser path at all and depends on
``read_text`` below.

Only text is supported here. Clipboard contents are passed through stdin or a
native memory buffer, never process arguments or logs. Missing display/tooling
returns ``False``/``None`` instead of raising, preserving headless operation.
"""
from __future__ import annotations

import ctypes
import logging
import shutil
import subprocess
import time
from collections.abc import Sequence

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.platform import detect_platform
from jarvis.platform.capabilities import detect_capabilities

log = logging.getLogger(__name__)

_COMMAND_TIMEOUT_S = 5.0
_WINDOWS_CLIPBOARD_RETRIES = 10
_WINDOWS_CLIPBOARD_RETRY_S = 0.01


def write_text(text: str) -> bool:
    """Replace the local desktop clipboard with *text*.

    Returns ``True`` only when the platform clipboard accepted the complete
    string. A headless host or unavailable OS integration returns ``False``.
    """
    if not detect_capabilities().display_present:
        log.info("clipboard: no display present; native copy is unavailable")
        return False

    platform = detect_platform()
    if platform == "win32":
        return _write_windows(text)
    if platform == "darwin":
        return _run_command(["/usr/bin/pbcopy"], text)
    return _write_linux(text)


def read_text() -> str | None:
    """Return the local desktop clipboard's text.

    ``None`` means this host cannot read a clipboard at all (headless, or no
    OS tooling); an empty string means the clipboard is genuinely empty. The
    caller must be able to distinguish those, so an unreachable clipboard is
    never reported as a successful empty paste.

    Line endings are normalised to ``\\n``. Windows hands out ``\\r\\n``, and a
    raw carriage return pasted into a terminal reads as a second Enter — which
    would run every line of a multi-line command twice.
    """
    if not detect_capabilities().display_present:
        log.info("clipboard: no display present; native paste is unavailable")
        return None

    platform = detect_platform()
    if platform == "win32":
        raw = _read_windows()
    elif platform == "darwin":
        raw = _read_command(["/usr/bin/pbpaste"])
    else:
        raw = _read_linux()

    if raw is None:
        return None
    return raw.replace("\r\n", "\n").replace("\r", "\n")


def _read_command(command: Sequence[str]) -> str | None:
    """Read clipboard text from a fixed OS command's stdout."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed, non-shell OS command
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_COMMAND_TIMEOUT_S,
            check=False,
            close_fds=True,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("clipboard: native paste command unavailable (%s)", exc)
        return None
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        log.warning(
            "clipboard: native paste command failed with exit %d%s",
            completed.returncode,
            f" ({detail[:300]})" if detail else "",
        )
        return None
    return completed.stdout


def _read_linux() -> str | None:
    """Use the available Wayland/X11 clipboard command, if any."""
    candidates = (
        ("wl-paste", ["wl-paste", "--no-newline"]),
        ("xclip", ["xclip", "-selection", "clipboard", "-out"]),
        ("xsel", ["xsel", "--clipboard", "--output"]),
    )
    for executable, command in candidates:
        resolved = shutil.which(executable)
        if resolved:
            command[0] = resolved
            return _read_command(command)
    log.info("clipboard: no wl-paste, xclip, or xsel command is available")
    return None


def _read_windows() -> str | None:
    """Read Unicode text with the Win32 clipboard API.

    The clipboard is a shared, singly-owned resource: brief retries handle
    another application holding it open, and the handle is always unlocked and
    the clipboard always closed, even on an unexpected format.
    """
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
    except (AttributeError, OSError) as exc:
        log.warning("clipboard: Win32 API unavailable (%s)", exc)
        return None

    opened = False
    for _attempt in range(_WINDOWS_CLIPBOARD_RETRIES):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(_WINDOWS_CLIPBOARD_RETRY_S)
    if not opened:
        log.warning("clipboard: Win32 clipboard remained busy")
        return None

    handle = None
    try:
        if not user32.IsClipboardFormatAvailable(13):  # CF_UNICODETEXT
            # Something non-textual is on the clipboard (an image, a file
            # list). There is nothing to paste as text, which is not a failure.
            return ""
        handle = user32.GetClipboardData(13)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    except (AttributeError, OSError, ValueError) as exc:
        log.warning("clipboard: Win32 clipboard read failed (%s)", exc)
        return None
    finally:
        user32.CloseClipboard()


def _run_command(command: Sequence[str], text: str) -> bool:
    """Feed clipboard text to a fixed OS command through UTF-8 stdin."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed, non-shell OS command
            list(command),
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_COMMAND_TIMEOUT_S,
            check=False,
            close_fds=True,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("clipboard: native copy command unavailable (%s)", exc)
        return False
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        log.warning(
            "clipboard: native copy command failed with exit %d%s",
            completed.returncode,
            f" ({detail[:300]})" if detail else "",
        )
        return False
    return True


def _write_linux(text: str) -> bool:
    """Use the available Wayland/X11 clipboard command, if any."""
    candidates = (
        ("wl-copy", ["wl-copy", "--type", "text/plain;charset=utf-8"]),
        ("xclip", ["xclip", "-selection", "clipboard", "-in"]),
        ("xsel", ["xsel", "--clipboard", "--input"]),
    )
    for executable, command in candidates:
        resolved = shutil.which(executable)
        if resolved:
            command[0] = resolved
            return _run_command(command, text)
    log.info("clipboard: no wl-copy, xclip, or xsel command is available")
    return False


def _write_windows(text: str) -> bool:
    """Write Unicode text with the Win32 clipboard API.

    ``SetClipboardData`` takes ownership of the movable allocation after a
    successful call; every failure path frees it locally. Brief retries handle
    another application momentarily holding the clipboard open.
    """
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL
    except (AttributeError, OSError) as exc:
        log.warning("clipboard: Win32 API unavailable (%s)", exc)
        return False

    opened = False
    for _attempt in range(_WINDOWS_CLIPBOARD_RETRIES):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(_WINDOWS_CLIPBOARD_RETRY_S)
    if not opened:
        log.warning("clipboard: Win32 clipboard remained busy")
        return False

    handle = None
    try:
        if not user32.EmptyClipboard():
            return False
        buffer = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buffer)
        handle = kernel32.GlobalAlloc(0x0002, size)  # GMEM_MOVEABLE
        if not handle:
            return False
        target = kernel32.GlobalLock(handle)
        if not target:
            return False
        try:
            ctypes.memmove(target, ctypes.addressof(buffer), size)
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(13, handle):  # CF_UNICODETEXT
            return False
        handle = None  # ownership transferred to the operating system
        return True
    except (AttributeError, OSError, ValueError) as exc:
        log.warning("clipboard: Win32 clipboard write failed (%s)", exc)
        return False
    finally:
        if handle:
            kernel32.GlobalFree(handle)
        user32.CloseClipboard()


__all__ = ["read_text", "write_text"]
