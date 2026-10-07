"""Put a picture on the local desktop clipboard so Ctrl/Cmd+V pastes it anywhere.

The embedded WebView cannot be trusted with ``navigator.clipboard.write`` for
images: WebView2 may never settle the promise, and WKWebView rejects it once a
click handler has awaited anything. So the desktop backend copies natively,
and it puts the SAME picture up in several formats at once, because every app
asks the clipboard for a different one:

- **Windows** — the registered ``PNG`` format first (lossless, keeps
  transparency; browsers, Electron chat apps and Office read it), then
  ``CF_DIB`` (the classic bitmap every Win32 app pastes; Windows derives
  ``CF_BITMAP`` and ``CF_DIBV5`` from it). Transparent pixels are laid on
  white in the bitmap, so they never paste as black.
- **macOS** — ``public.png`` plus ``public.tiff`` on the general pasteboard,
  set through the system's JavaScript for Automation bridge (``osascript -l
  JavaScript``); no extra package. TIFF is the pasteboard's native image type
  that older Cocoa apps read. Should the bridge fail, AppleScript sets the PNG
  alone.
- **Linux** — ``image/png`` through ``wl-copy`` on Wayland, else ``xclip``.
  Both keep a small process alive that serves the picture until something
  else is copied; X11/Wayland apps all read ``image/png``. Without either tool
  the copy reports ``no_tool`` and says which package to install.

:func:`copy_image` never raises and says why a copy did not happen; a headless
host returns ``no_display`` without touching any OS integration.
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
from dataclasses import dataclass
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
#: Pasteboard type -> file suffix for the macOS bridge's staged files.
_MACOS_TYPES = (("public.png", ".png"), ("public.tiff", ".tiff"))

#: Sets every (type, file) pair given as arguments on the general pasteboard
#: and prints the types it took. Arguments travel as ``argv``, never spliced
#: into the script source. The pasteboard is cleared only once some data
#: loaded, so a failure never leaves it empty. Zero-argument methods are
#: called by property access (the bridge's idiom); should ``clearContents``
#: not run, ``setDataForType`` refuses (only the owner may write), nothing is
#: printed and the AppleScript fallback takes over.
_MACOS_BRIDGE = """
function run(argv) {
  ObjC.import('AppKit');
  var items = [];
  for (var i = 0; i + 1 < argv.length; i += 2) {
    var data = $.NSData.dataWithContentsOfFile(argv[i + 1]);
    if (!data.isNil()) { items.push([argv[i], data]); }
  }
  if (items.length === 0) { return ''; }
  var board = $.NSPasteboard.generalPasteboard;
  board.clearContents;
  var put = [];
  for (var j = 0; j < items.length; j++) {
    if (board.setDataForType(items[j][1], items[j][0])) { put.push(items[j][0]); }
  }
  return put.join(' ');
}
"""

#: User-facing reasons, keyed by :attr:`CopyResult.reason`.
REASON_MESSAGES = {
    "": "Copied to the clipboard.",
    "not_png": "Only PNG pictures can be copied.",
    "no_display": "There is no desktop on this computer, so there is no clipboard.",
    "no_tool": "Copying pictures needs 'wl-clipboard' (Wayland) or 'xclip' (X11) installed.",
    "busy": "Another app is holding the clipboard; try again.",
    "unreadable": "The picture could not be read.",
    "refused": "The clipboard refused the picture.",
    "unavailable": "The clipboard is not reachable.",
}


@dataclass(frozen=True, slots=True)
class CopyResult:
    """What a copy did: ``ok``, the formats the OS took, or why it failed."""

    ok: bool
    #: ``""`` on success, else one key of :data:`REASON_MESSAGES`.
    reason: str = ""
    formats: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        return REASON_MESSAGES.get(self.reason, REASON_MESSAGES["refused"])


def is_png(data: bytes) -> bool:
    return data.startswith(_PNG_SIGNATURE)


def copy_image(png: bytes) -> CopyResult:
    """Replace the clipboard with this PNG, in every format the OS offers."""
    if not is_png(png):
        log.warning("clipboard-image: refusing a payload that is not a PNG")
        return CopyResult(False, "not_png")
    if not detect_capabilities().display_present:
        log.info("clipboard-image: no display present; native copy is unavailable")
        return CopyResult(False, "no_display")
    platform = detect_platform()
    if platform == "win32":
        result = _write_windows(png)
    elif platform == "darwin":
        result = _write_macos(png)
    else:
        result = _write_linux(png)
    if result.ok:
        log.info("clipboard-image: copied as %s", ", ".join(result.formats))
    return result


def write_png(png: bytes) -> bool:
    """Replace the clipboard with this PNG. ``True`` only when the OS took it."""
    return copy_image(png).ok


def _has_alpha(image) -> bool:  # noqa: ANN001 - a PIL image; Pillow loads lazily
    return image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info


def _flattened(png: bytes):  # noqa: ANN202 - a PIL image; Pillow loads lazily
    """The picture as opaque RGB, transparent parts laid on white."""
    from PIL import Image  # noqa: PLC0415 - Pillow only when an image is copied

    with Image.open(io.BytesIO(png)) as image:
        if _has_alpha(image):
            rgba = image.convert("RGBA")
            backdrop = Image.new("RGB", rgba.size, (255, 255, 255))
            backdrop.paste(rgba, mask=rgba.getchannel("A"))
            return backdrop
        return image.convert("RGB")


def png_to_dib(png: bytes) -> bytes:
    """The PNG as a ``CF_DIB`` payload: a BMP file without its 14-byte header."""
    out = io.BytesIO()
    _flattened(png).save(out, format="BMP")
    return out.getvalue()[14:]


def png_to_tiff(png: bytes) -> bytes:
    """The PNG as a TIFF (alpha kept), LZW-compressed where Pillow has libtiff."""
    from PIL import Image  # noqa: PLC0415

    with Image.open(io.BytesIO(png)) as image:
        converted = image.convert("RGBA" if _has_alpha(image) else "RGB")
    out = io.BytesIO()
    try:
        converted.save(out, format="TIFF", compression="tiff_lzw")
    except (OSError, ValueError):
        # A Pillow without libtiff writes uncompressed TIFF only.
        out = io.BytesIO()
        converted.save(out, format="TIFF")
    return out.getvalue()


def _run(command: list[str], data: bytes | None = None) -> tuple[bool, str]:
    """Run a fixed OS command; ``(ok, stdout)``.

    stdout and stderr go to an anonymous file, never a pipe: ``xclip`` and
    ``wl-copy`` fork a child that serves the clipboard and inherits both, so a
    pipe would stay open until the next copy and ``run`` would wait for it.
    """
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            completed = subprocess.run(  # noqa: S603 - fixed, non-shell OS command
                command,
                input=data if data is not None else b"",
                stdout=out,
                stderr=err,
                timeout=_COMMAND_TIMEOUT_S,
                check=False,
                close_fds=True,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            out.seek(0)
            err.seek(0)
            stdout = out.read(4096).decode("utf-8", errors="replace").strip()
            detail = err.read(4096).decode("utf-8", errors="replace").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("clipboard-image: copy command unavailable (%s)", exc)
        return False, ""
    if completed.returncode != 0:
        log.warning(
            "clipboard-image: copy command failed with exit %d%s",
            completed.returncode,
            f" ({detail[:300]})" if detail else "",
        )
        return False, ""
    return True, stdout


def _write_linux(png: bytes) -> CopyResult:
    wl_copy = shutil.which("wl-copy")
    xclip = shutil.which("xclip")
    if wl_copy and os.environ.get("WAYLAND_DISPLAY"):
        command = [wl_copy, "--type", "image/png"]
    elif xclip:
        command = [xclip, "-selection", "clipboard", "-t", "image/png", "-i"]
    elif wl_copy:
        command = [wl_copy, "--type", "image/png"]
    else:
        log.info("clipboard-image: neither wl-copy nor xclip is installed")
        return CopyResult(False, "no_tool")
    ok, _stdout = _run(command, png)
    return CopyResult(True, formats=("image/png",)) if ok else CopyResult(False, "refused")


def _write_macos(png: bytes) -> CopyResult:
    staged: list[Path] = []
    try:
        payloads = {"public.png": png}
        try:
            payloads["public.tiff"] = png_to_tiff(png)
        except Exception:  # noqa: BLE001 - the PNG alone still pastes in most apps
            log.info("clipboard-image: no TIFF rendition; copying the PNG alone", exc_info=True)
        args: list[str] = []
        for kind, suffix in _MACOS_TYPES:
            data = payloads.get(kind)
            if data is None:
                continue
            handle, name = tempfile.mkstemp(prefix="jarvis-clip-", suffix=suffix)
            path = Path(name)
            staged.append(path)
            with os.fdopen(handle, "wb") as file:
                file.write(data)
            args += [kind, str(path)]
        ok, stdout = _run(["/usr/bin/osascript", "-l", "JavaScript", "-e", _MACOS_BRIDGE, *args])
        formats = tuple(kind for kind in stdout.split() if kind in payloads)
        if ok and "public.png" in formats:
            return CopyResult(True, formats=formats)
        log.info("clipboard-image: pasteboard bridge did not take the PNG; using AppleScript")
        script = (
            "on run argv\n"
            "set the clipboard to (read (POSIX file (item 1 of argv)) as «class PNGf»)\n"
            "end run"
        )
        ok, _stdout = _run(["/usr/bin/osascript", "-e", script, str(staged[0])])
        return CopyResult(True, formats=("public.png",)) if ok else CopyResult(False, "refused")
    except OSError as exc:
        log.warning("clipboard-image: could not stage the picture for osascript (%s)", exc)
        return CopyResult(False, "unavailable")
    finally:
        for path in staged:
            path.unlink(missing_ok=True)


def _write_windows(png: bytes) -> CopyResult:
    try:
        dib = png_to_dib(png)
    except Exception as exc:  # noqa: BLE001 - a picture Pillow cannot read is not copyable
        log.warning("clipboard-image: could not convert the PNG to a bitmap (%s)", exc)
        return CopyResult(False, "unreadable")
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
        return CopyResult(False, "unavailable")

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
        return CopyResult(False, "busy")
    try:
        if not user32.EmptyClipboard():
            log.warning("clipboard-image: EmptyClipboard failed")
            return CopyResult(False, "refused")
        formats: list[str] = []
        # Format order is the offer order apps see: the lossless PNG first.
        png_format = user32.RegisterClipboardFormatW("PNG")
        if png_format and put(png_format, png):
            formats.append("PNG")
        else:
            log.info("clipboard-image: the PNG format was not accepted")
        if put(_CF_DIB, dib):
            formats.append("CF_DIB")
        else:
            log.warning("clipboard-image: SetClipboardData(CF_DIB) failed")
        return CopyResult(True, formats=tuple(formats)) if formats else CopyResult(False, "refused")
    finally:
        user32.CloseClipboard()


__all__ = [
    "REASON_MESSAGES",
    "CopyResult",
    "copy_image",
    "is_png",
    "png_to_dib",
    "png_to_tiff",
    "write_png",
]
