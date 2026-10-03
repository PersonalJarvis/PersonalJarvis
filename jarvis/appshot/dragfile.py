"""The one place an appshot touches disk: a file for a drag into another app.

A WebView cannot drag a real file out, so the native drag bridge
(``jarvis/ui/native_drag.py``) needs a path. The corner card writes its
picture here when it is dragged, and the editor's "Drag me" handle writes the
edited picture here the moment the user presses it. Nothing is written
otherwise; files older than an hour are removed on the next write, so the
folder never grows (see ``docs/appshots.md``).
"""

from __future__ import annotations

import logging
import tempfile
import time
from contextlib import suppress
from pathlib import Path

log = logging.getLogger(__name__)

#: Files older than this are removed before the next one is written.
MAX_AGE_S = 3600.0


def drag_folder() -> Path:
    return Path(tempfile.gettempdir()) / "jarvis-appshots"


def write_drag_file(png: bytes, *, now: float | None = None) -> Path | None:
    """Write ``png`` for one drag and return its path, or None on failure."""
    folder = drag_folder()
    moment = time.time() if now is None else now
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("appshot-*.png"):
            with suppress(OSError):  # a file still open elsewhere stays
                if old.stat().st_mtime < moment - MAX_AGE_S:
                    old.unlink()
        stem = time.strftime("appshot-%Y%m%d-%H%M%S", time.localtime(moment))
        path = folder / f"{stem}.png"
        counter = 1
        while path.exists():
            counter += 1
            path = folder / f"{stem}-{counter}.png"
        path.write_bytes(png)
        return path
    except OSError as exc:
        log.warning("appshot: could not write the drag file (%s)", exc)
        return None


__all__ = ["MAX_AGE_S", "drag_folder", "write_drag_file"]
