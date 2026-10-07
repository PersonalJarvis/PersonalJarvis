"""Where Jarvis X keeps its files.

Two locations:

- the **save folder** for the captures themselves — ``[jarvisx].save_dir`` when
  set, else ``Pictures/Jarvis X`` resolved per OS (the Windows known-folder
  API, so a OneDrive-redirected Pictures folder is honoured; the XDG
  ``PICTURES`` entry on Linux; ``~/Pictures`` on macOS), else a folder in the
  Jarvis data directory when the machine has no Pictures folder at all (a
  headless server);
- the **library folder** in the Jarvis data directory, holding the index and
  the small preview thumbnails.

Only stdlib at module scope; the OS folder lookup lives in
:mod:`jarvis.platform.user_dirs`.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

#: Folder name inside Pictures (and the file-name prefix of every capture).
FOLDER_NAME = "Jarvis X"


def library_dir() -> Path:
    """``<data dir>/jarvisx`` — the index database and the thumbnails."""
    from jarvis.core import config as config_module  # noqa: PLC0415

    return Path(config_module.DATA_DIR) / "jarvisx"


def default_save_dir() -> Path:
    """``Pictures/Jarvis X`` when a Pictures folder exists, else the data dir."""
    pictures = pictures_dir()
    if pictures is not None:
        return pictures / FOLDER_NAME
    return library_dir() / "captures"


def resolve_save_dir(configured: str) -> Path:
    """The folder new captures go to, created on demand.

    A configured folder that cannot be created falls back to the default one
    (logged), so a stale setting never loses a capture.
    """
    candidates: list[Path] = []
    text = str(configured or "").strip()
    if text:
        candidates.append(Path(os.path.expandvars(text)).expanduser())
    candidates.append(default_save_dir())
    candidates.append(library_dir() / "captures")
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            log.warning("jarvisx: save folder %s is not usable, trying the next one", candidate)
    # The last candidate lives in the data dir; if even that fails the caller's
    # write reports the real OS error.
    return candidates[-1]


def pictures_dir() -> Path | None:
    """The user's Pictures folder, or ``None`` when this machine has none."""
    from jarvis.platform.user_dirs import pictures_dir as user_pictures  # noqa: PLC0415

    try:
        found = user_pictures()
    except Exception:  # noqa: BLE001 - a probe failure means "use the fallback"
        log.debug("jarvisx: Pictures folder probe failed", exc_info=True)
        return None
    if found is not None and found.is_dir():
        return found
    return None


def capture_filename(kind: str, when: datetime, ext: str) -> str:
    """``Jarvis X 2026-09-29 at 14.03.22.png`` (``Recording`` for videos)."""
    label = "Recording" if kind == "video" else FOLDER_NAME
    stamp = when.strftime("%Y-%m-%d at %H.%M.%S")
    return f"{label} {stamp}.{ext}"


def unique_path(folder: Path, filename: str) -> Path:
    """``folder/filename``, suffixed `` (2)``, `` (3)`` … when it already exists."""
    candidate = folder / filename
    if not candidate.exists():
        return candidate
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    for index in range(2, 1000):
        candidate = folder / (f"{stem} ({index}).{ext}" if ext else f"{stem} ({index})")
        if not candidate.exists():
            return candidate
    return folder / (f"{stem} {os.getpid()}.{ext}" if ext else f"{stem} {os.getpid()}")


def edited_path_for(original: Path) -> Path:
    """``<name>-edited.png`` next to the original."""
    return original.with_name(f"{original.stem}-edited.png")


__all__ = [
    "FOLDER_NAME",
    "capture_filename",
    "default_save_dir",
    "edited_path_for",
    "library_dir",
    "pictures_dir",
    "resolve_save_dir",
    "unique_path",
]
