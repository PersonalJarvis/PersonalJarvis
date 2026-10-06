"""Small OS duties of the PySide6 sidecars: the area picker, corner card, recorder.

* :func:`hide_from_dock` — on macOS a plain Python process that opens a Qt
  window becomes a foreground app with its own "Python" Dock icon and
  app-switcher entry. Each resident sidecar turns itself into an accessory
  app (windows, no Dock icon, no menu bar), the way the Jarvis bar does.
* :func:`missing_system_library` — on Linux X11, Qt 6.5+ needs the system
  library ``libxcb-cursor0``. Without it Qt aborts the process (SIGABRT) before
  any Python handler runs, so the sidecar just "stopped unexpectedly". The
  capability checks ask this first and name the package instead.
* :func:`stderr_sink` / :func:`crash_hint` — sidecar stderr goes to
  ``<data dir>/logs/<name>.log`` (the latest run) instead of nowhere, and a
  crash message points at that file.

Nothing here imports Qt or initialises at import time (AP-26).
"""

from __future__ import annotations

import ctypes.util
import logging
import os
import sys
from pathlib import Path
from typing import IO

log = logging.getLogger(__name__)

_SINKS: dict[str, IO[bytes]] = {}

#: Shown when Qt's X11 platform plugin cannot load for want of xcb-cursor.
XCB_CURSOR_MESSAGE = (
    "Qt needs the system library libxcb-cursor0 to open windows on X11 "
    "(Debian/Ubuntu: sudo apt install libxcb-cursor0; Fedora and Arch: "
    "xcb-util-cursor)"
)


def hide_from_dock() -> None:
    """macOS: make this process an accessory app (no Dock icon). No-op elsewhere."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication  # type: ignore[import-not-found]  # noqa: PLC0415

        # 1 = NSApplicationActivationPolicyAccessory: windows allowed, no Dock
        # icon, no menu bar takeover.
        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception:  # noqa: BLE001 - without pyobjc the icon simply stays
        log.debug("qt-sidecar: Dock icon hide skipped (pyobjc unavailable?)", exc_info=True)


def missing_system_library() -> str:
    """Why Qt cannot open a window on this Linux X11 desktop, or ``""``."""
    if not sys.platform.startswith("linux"):
        return ""
    if os.environ.get("WAYLAND_DISPLAY") or not os.environ.get("DISPLAY"):
        return ""
    try:
        if ctypes.util.find_library("xcb-cursor") is None:
            return XCB_CURSOR_MESSAGE
    except Exception:  # noqa: BLE001 - an unanswerable probe lets Qt decide
        log.debug("qt-sidecar: library probe failed", exc_info=True)
    return ""


def stderr_log_path(name: str) -> Path:
    from jarvis.core.config import DATA_DIR  # noqa: PLC0415

    return Path(DATA_DIR) / "logs" / f"{name}.log"


def stderr_sink(name: str) -> IO[bytes] | int:
    """A file for a sidecar's stderr (latest run only); DEVNULL when none can open.

    The handle is kept until the next spawn of the same sidecar replaces it,
    so the parent never leaks one per restart.
    """
    import subprocess  # noqa: PLC0415

    previous = _SINKS.pop(name, None)
    if previous is not None:
        previous.close()
    try:
        path = stderr_log_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        sink = path.open("wb")
    except OSError:
        log.debug("qt-sidecar: no log file for %s; stderr is dropped", name, exc_info=True)
        return subprocess.DEVNULL
    _SINKS[name] = sink
    return sink


def crash_hint(code: int | None, name: str) -> str:
    """One extra sentence for a sidecar that died on a signal, or ``""``."""
    if code is None or code >= 0:
        return ""
    missing = missing_system_library()
    if missing:
        return f" {missing}."
    return f" Details are in {stderr_log_path(name)}."


__all__ = [
    "XCB_CURSOR_MESSAGE",
    "crash_hint",
    "hide_from_dock",
    "missing_system_library",
    "stderr_log_path",
    "stderr_sink",
]
