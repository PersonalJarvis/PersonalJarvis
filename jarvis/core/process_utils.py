"""Cross-platform helpers for the process quirks a desktop app hits.

The helpers cover a windowed Python process without standard streams, spawning
subprocesses without flashing console windows (``NO_WINDOW_CREATIONFLAGS``),
and stopping Windows from replacing an unresponsive window with an opaque ghost
(``disable_windows_app_ghosting``). The Windows-only helpers are no-ops elsewhere.

Background:
    The desktop app runs under ``pythonw.exe`` (no attached console). When a
    child process is started without explicit ``creationflags``, Windows
    allocates a fresh console window for every child — for ``npx``, ``git``,
    ``uvx`` and CLI probes that means a flicker storm of black terminals
    popping up and closing during normal startup.

    Setting ``CREATE_NO_WINDOW`` on every spawn makes children silently
    inherit no-console state. ``asyncio.create_subprocess_exec`` accepts the
    same Windows constants as ``subprocess.Popen``.

Usage:
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )

On non-Windows platforms ``NO_WINDOW_CREATIONFLAGS`` is ``0`` and the
parameter is silently ignored by the subprocess machinery.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TextIO

_NULL_STANDARD_STREAMS: list[TextIO] = []


def ensure_standard_streams() -> None:
    """Make ``stdout`` and ``stderr`` safe in a windowed Python process.

    ``pythonw.exe`` and windowed PyInstaller bootloaders expose both streams as
    ``None``. Libraries such as Uvicorn still call ``sys.stdout.isatty()`` while
    configuring logging, and our own fatal-startup paths write to stderr. Give
    those callers a real text stream backed by the platform null device. The
    desktop file log remains the durable diagnostic surface; this only prevents
    no-console launches from crashing while trying to report a crash.

    Existing streams are preserved. On Windows, reconfigure them to UTF-8 when
    the stream supports it, matching the historical launcher behavior.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            replacement = open(  # noqa: SIM115 - intentionally process-lifetime
                os.devnull,
                "w",
                encoding="utf-8",
                errors="replace",
            )
            setattr(sys, name, replacement)
            _NULL_STANDARD_STREAMS.append(replacement)
            continue
        if sys.platform == "win32":
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                # No reconfigure support or a closed/redirected stream — the
                # original stream keeps working, just without forced UTF-8.
                pass


if sys.platform == "win32":
    NO_WINDOW_CREATIONFLAGS: int = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
else:
    NO_WINDOW_CREATIONFLAGS = 0


def resolve_executable(name: str) -> str:
    """Resolve a binary name to its full on-disk path, honoring PATHEXT.

    On Windows, many CLIs ship as ``.cmd`` / ``.bat`` / ``.ps1`` shims (gcloud,
    npm, vercel, firebase, ...). ``asyncio.create_subprocess_exec`` /
    ``subprocess`` with ``shell=False`` do NOT perform PATH + PATHEXT lookup the
    way the shell does — passing a bare ``"gcloud"`` raises
    ``FileNotFoundError`` even though ``gcloud.cmd`` is on PATH. ``shutil.which``
    DOES honor PATHEXT, so resolving the name to its full path first lets us
    exec a ``.cmd``/``.bat`` shim directly.

    Returns the resolved absolute path when found, otherwise the original name
    unchanged (so the caller still raises a clean ``FileNotFoundError`` instead
    of silently swallowing a typo).
    """
    if not name:
        return name
    resolved = shutil.which(name)
    if resolved:
        return resolved

    # A caller may launch the app as ``.venv/bin/python`` without activating
    # that environment, so the running interpreter's directory is absent from
    # PATH. Treat its exact basename as resolvable even in that valid setup.
    if (
        Path(name).name == name
        and Path(sys.executable).name.casefold() == name.casefold()
    ):
        return str(Path(sys.executable).resolve())
    return name


_ghosting_disabled = False


def disable_windows_app_ghosting() -> bool:
    """Stop Windows swapping an unresponsive window for an opaque ghost.

    When a top-level window stops pumping messages for roughly five seconds,
    Windows hides it and puts a stand-in window of class ``Ghost`` at the exact
    same rectangle, painted by the DWM rather than by the app. That stand-in is
    an ordinary window: it does **not** inherit ``WS_EX_LAYERED``, so the Jarvis
    Bar's magenta colour key is never applied to it, and the full window
    rectangle lands on screen as an opaque BLACK box around the pill.

    Reaching this does not require the app to be broken. The bar paints from a
    Tk loop, and that loop stops pumping whenever *another* thread holds the GIL
    through a long CPU-bound stretch — a slow config load on the backend thread
    is enough. The user sees a black rectangle around their bar and nothing else
    wrong, which reads as a rendering bug rather than as a stall.

    ``DisableProcessWindowsGhosting`` is process-wide and cannot be undone. That
    is the right trade for this app: a frameless click-through overlay gains
    nothing from a ghost — there is no title bar to grey out and no close button
    to offer — while the desktop window keeps its own Restart control, the tray
    icon, and Task Manager as ways out of a hang.

    Idempotent and never raises. Returns ``True`` when ghosting is off for this
    process, ``False`` on a platform that has no such behaviour (macOS, Linux)
    or when the call could not be made.
    """
    global _ghosting_disabled
    if _ghosting_disabled:
        return True
    if sys.platform != "win32":
        # No equivalent exists: macOS shows a spinning cursor and Linux WMs
        # offer their own "not responding" prompt, neither of which repaints
        # the window's own pixels.
        return False
    try:
        import ctypes  # noqa: PLC0415 — Windows-only, keep it off the import floor

        ctypes.windll.user32.DisableProcessWindowsGhosting()
    except Exception:  # noqa: BLE001 — cosmetic hardening; never block a UI boot
        return False
    _ghosting_disabled = True
    return True


__all__ = [
    "NO_WINDOW_CREATIONFLAGS",
    "disable_windows_app_ghosting",
    "ensure_standard_streams",
    "resolve_executable",
]
