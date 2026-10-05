"""The one place an appshot touches disk: a file for a drag into another app.

A WebView cannot drag a real file out, so the native drag bridge
(``jarvis/ui/native_drag.py``) needs a path. The corner card writes its
picture here when it is dragged, and the editor's "Drag me" handle writes the
edited picture here the moment the user presses it. Nothing is written
otherwise; files older than an hour are removed on the next write, so the
folder never grows (see ``docs/appshots.md``).

The folder is private to the user. A shared ``/tmp`` on Linux is readable by
every account on the machine, so on POSIX the folder carries the user id, is
created ``0700`` and is used only when this user owns it and nobody else can
read it — a folder someone else created first is refused, never written into.
Windows' temp folder is already per user.
"""

from __future__ import annotations

import logging
import os
import stat
import tempfile
import time
from contextlib import suppress
from pathlib import Path

log = logging.getLogger(__name__)

#: Files older than this are removed before the next one is written.
MAX_AGE_S = 3600.0


def drag_folder() -> Path:
    """Where drag files go (not created here): per user on every OS."""
    base = Path(tempfile.gettempdir())
    if os.name == "nt":
        return base / "jarvis-appshots"
    return base / f"jarvis-appshots-{os.getuid()}"


def prepare_drag_folder() -> Path:
    """:func:`drag_folder`, created and private; ``OSError`` when it cannot be."""
    folder = drag_folder()
    if os.name == "nt":
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    try:
        folder.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        pass
    info = os.lstat(folder)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OSError(f"{folder} belongs to someone else; no drag file is written there")
    if info.st_mode & 0o077:
        os.chmod(folder, 0o700)
    return folder


def write_drag_file(png: bytes, *, now: float | None = None) -> Path | None:
    """Write ``png`` for one drag and return its path, or None on failure."""
    moment = time.time() if now is None else now
    try:
        folder = prepare_drag_folder()
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


__all__ = ["MAX_AGE_S", "drag_folder", "prepare_drag_folder", "write_drag_file"]
