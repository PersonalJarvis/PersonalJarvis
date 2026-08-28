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
            # Recorded in _NULL_STANDARD_STREAMS so callers can ask whether what
            # they just printed actually reached a human — see
            # ``standard_error_is_visible``.
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


def standard_error_is_visible() -> bool:
    """Can a human read what we write to ``sys.stderr``?

    False in a windowed process — ``pythonw.exe``, a GUI PyInstaller build — where
    ``ensure_standard_streams`` had to substitute the null device, so everything
    printed there is discarded. Code that reports a fatal startup problem uses
    this to decide whether stderr was enough or whether the message needs a
    second, visible surface (a dialog). Getting that decision from the stream we
    actually wrote to beats guessing from a console handle: a redirected build
    log, a pipe and an IDE console are all legitimately readable, and none of
    them own a console window.
    """
    stream = getattr(sys, "stderr", None)
    if stream is None or getattr(stream, "closed", False):
        return False
    return not any(stream is null for null in _NULL_STANDARD_STREAMS)


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


_WM_NULL = 0x0000


def thread_message_loop_wake_supported() -> bool:
    """True where :func:`wake_thread_message_loop` can actually wake a thread.

    Only Windows has a per-thread message queue that a blocked ``GetMessage``
    waits on; the X11 and Aqua notifiers wait on a socket/pipe and a run loop
    respectively, and neither has shown the lost-timer sleep this call exists
    for. Callers use this to skip the waker thread entirely instead of running
    a loop that can never act.
    """
    return sys.platform == "win32"


def wake_thread_message_loop(native_thread_id: int) -> bool:
    """Make a thread blocked in ``GetMessage`` return once, without side effects.

    Posts ``WM_NULL`` — a message every window procedure ignores — to the
    thread's queue with ``PostThreadMessageW``. The one thing it changes is that
    the thread's blocking ``GetMessage`` returns, which lets an event loop that
    sits on top of it (Tcl's Windows notifier, for the Jarvis Bar) run its
    overdue timers. The Jarvis Bar's Tk thread was found asleep in exactly that
    call with every ``after`` chain armed and none firing: Tcl's timer wake-up
    had been lost once, and nothing else on a settled idle bar generates a
    message, so the bar stayed frozen on its idle pill for the rest of the
    session (BUG-202). One posted message revived it.

    Non-blocking and never raises. Returns ``True`` when the message was
    queued, ``False`` off Windows, when the thread has no message queue yet, or
    when the call could not be made.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes  # noqa: PLC0415 — Windows-only, keep it off the import floor

        return bool(ctypes.windll.user32.PostThreadMessageW(int(native_thread_id), _WM_NULL, 0, 0))
    except Exception:  # noqa: BLE001 — a failed nudge is reported, never raised
        return False


__all__ = [
    "NO_WINDOW_CREATIONFLAGS",
    "disable_windows_app_ghosting",
    "ensure_standard_streams",
    "resolve_executable",
    "thread_message_loop_wake_supported",
    "wake_thread_message_loop",
]
