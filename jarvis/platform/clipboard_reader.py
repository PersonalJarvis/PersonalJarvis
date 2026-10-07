"""Bound Windows clipboard reads without leaving a thread holding it open.

Windows may spend 30 seconds asking a clipboard owner to render text. The
reader is disposable: subprocess.run kills and reaps it on timeout, releasing
the clipboard before dictation writes its transcript. No clipboard content is
written to disk, command arguments or logs. Native reads remain in clipboard.py.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)
READ_TIMEOUT_S = 1.0
_READ_LOCK = threading.Lock()


def _reader_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--clipboard-read"]
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return [str(executable), "-m", "jarvis.platform.clipboard_reader"]


def read_with_deadline() -> str | None:
    """Return text, or None on a busy/unresponsive clipboard; never queue reads."""
    if not _READ_LOCK.acquire(blocking=False):
        log.debug("clipboard: another bounded read is in progress")
        return None
    try:
        result = subprocess.run(
            _reader_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=READ_TIMEOUT_S,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            # A desktop shortcut may start in another directory. Source installs
            # must import the same checkout as their parent (also in tests).
            cwd=None if getattr(sys, "frozen", False) else str(Path(__file__).resolve().parents[2]),
        )
        if result.returncode:
            log.warning("clipboard: read helper exited with code %d", result.returncode)
            return None
        value = json.loads(result.stdout)
        return value if isinstance(value, str) else None
    except subprocess.TimeoutExpired:
        log.warning("clipboard: read exceeded %.1fs; reader stopped", READ_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError, ValueError):
        # Do not log stdout, exception payloads or clipboard contents.
        log.warning("clipboard: read helper unavailable or returned invalid data")
    finally:
        _READ_LOCK.release()
    return None


def _write_response(value: str | None) -> None:
    payload = json.dumps(value, ensure_ascii=True).encode("utf-8")
    if sys.stdout is not None:
        sys.stdout.write(payload.decode("utf-8"))
        sys.stdout.flush()
        return

    # A windowed PyInstaller executable sets sys.stdout to None even when the
    # parent supplied a pipe. Write only to that inherited pipe, never a file.
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    buffer = ctypes.create_string_buffer(payload)
    written = wintypes.DWORD()
    if not kernel32.WriteFile(handle, buffer, len(payload), ctypes.byref(written), None):
        raise OSError("clipboard response pipe is unavailable")
    if written.value != len(payload):
        raise OSError("clipboard response was incomplete")


def main() -> int:
    from jarvis.platform.clipboard import _read_windows_native

    _write_response(_read_windows_native())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
