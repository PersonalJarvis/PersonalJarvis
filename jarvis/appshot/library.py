"""The appshot library — every appshot the user took, and its edited version.

The in-memory store (:mod:`jarvis.appshot.store`) only holds the last few
pictures for minutes. The library is the history the Appshots page shows as a
gallery: each appshot taken with the shortcuts, the page's buttons or the
assistant's ``take_appshot`` tool is written here once, and an edit saved in
the editor is kept beside it. A turn that looked at the screen on its own is
never written — only what the user (or the assistant on their request) chose
to capture.

It is switchable (``[appshot].library``), capped at :data:`MAX_ENTRIES`
(oldest removed first) and every entry can be deleted from the page; the
pictures are the finished, privacy-filtered appshots, never a raw frame.

Layout, one folder per appshot under ``<data dir>/appshots/``::

    <id>/meta.json                         what the page shows (no pixels)
    <id>/appshot-<taken>.<ext>             the appshot as captured
    <id>/appshot-<taken>-edited.png        the last edit saved in the editor
    <id>/thumb-*.jpg                       gallery thumbnails, made on first view

The picture files carry the capture time in their names because a drag hands
the file itself to a chat, a terminal agent or another app, where
``appshot-20261003-114747.png`` says what it is and ``original.png`` does not.

Nothing here is imported at boot (AP-26).
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jarvis.appshot.store import Appshot

log = logging.getLogger(__name__)

#: How many appshots the library keeps; the oldest go first.
MAX_ENTRIES = 500
#: Longest edge of a gallery thumbnail, in pixels.
THUMB_EDGE = 480

Variant = Literal["original", "edited"]

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_MIMES = {ext: mime for mime, ext in _EXTENSIONS.items()}

_lock = threading.Lock()
_root_override: Path | None = None


def library_root() -> Path:
    """``<data dir>/appshots`` — per app instance, like every runtime store."""
    if _root_override is not None:
        return _root_override
    from jarvis.core.config import DATA_DIR  # noqa: PLC0415

    return Path(DATA_DIR) / "appshots"


def set_root(path: Path | None) -> None:
    """Point the library elsewhere (tests); ``None`` restores the default."""
    global _root_override
    _root_override = path


@dataclass(frozen=True, slots=True)
class LibraryItem:
    """One picture in the gallery: an appshot, or the edit of one."""

    id: str
    variant: Variant
    path: Path
    mime: str
    width: int
    height: int
    label: str
    app_name: str
    trigger: str
    taken_at: float
    #: When the edit was saved; ``0`` for an original.
    edited_at: float
    #: True for an original that also has an edited version.
    has_edit: bool

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "variant": self.variant,
            "path": str(self.path),
            "mime": self.mime,
            "width": self.width,
            "height": self.height,
            "label": self.label,
            "app_name": self.app_name,
            "trigger": self.trigger,
            "taken_at": self.taken_at,
            "edited_at": self.edited_at,
            "has_edit": self.has_edit,
        }


def valid_id(shot_id: str) -> bool:
    """Only ids this module could have written — never a path fragment."""
    return bool(_ID_RE.match(shot_id or ""))


def _folder(shot_id: str) -> Path:
    if not valid_id(shot_id):
        raise ValueError("invalid appshot id")
    return library_root() / shot_id


def _read_meta(folder: Path) -> dict[str, object] | None:
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):  # a half-written or foreign folder is simply not listed
        return None
    return meta if isinstance(meta, dict) else None


def _write_meta(folder: Path, meta: dict[str, object]) -> None:
    tmp = folder / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    tmp.replace(folder / "meta.json")


def _stem(taken_at: float) -> str:
    return time.strftime("appshot-%Y%m%d-%H%M%S", time.localtime(taken_at))


def _named(folder: Path, meta: dict[str, object], key: str) -> Path | None:
    """The picture ``meta[key]`` names, when it is a plain file name that exists."""
    name = meta.get(key)
    if not isinstance(name, str) or not name or Path(name).name != name:
        return None
    candidate = folder / name
    return candidate if candidate.is_file() else None


def _original_file(folder: Path, meta: dict[str, object]) -> Path | None:
    named = _named(folder, meta, "file")
    if named is not None:
        return named
    for ext in _MIMES:  # entries written before the files carried their time
        candidate = folder / f"original.{ext}"
        if candidate.is_file():
            return candidate
    return None


def _edited_file(folder: Path, meta: dict[str, object]) -> Path | None:
    named = _named(folder, meta, "edited_file")
    if named is not None:
        return named
    legacy = folder / "edited.png"
    return legacy if legacy.is_file() else None


def _drop_thumbs(folder: Path, variant: Variant) -> None:
    for thumb in folder.glob(f"thumb-{variant}*.jpg"):
        try:
            thumb.unlink()
        except OSError as exc:
            log.debug("appshot library: stale thumbnail kept (%s)", exc)


def _base_meta(shot: Appshot) -> dict[str, object]:
    return {
        "id": shot.id,
        "label": shot.label,
        "app_name": shot.app_name,
        "trigger": shot.trigger,
        "taken_at": shot.taken_at,
    }


def save(shot: Appshot) -> bool:
    """Write a freshly taken appshot. Never raises; False when nothing was kept."""
    try:
        folder = _folder(shot.id)
        # The lossless copy when there is one: full resolution, PNG, the
        # monitor's colour profile. The model's JPEG is the fallback.
        lossless = bool(shot.original_png)
        image = shot.original_png if lossless else shot.image
        mime = "image/png" if lossless else shot.mime
        ext = _EXTENSIONS.get(mime, "jpg")
        width, height = _png_size(image) if lossless else (shot.width, shot.height)
        with _lock:
            folder.mkdir(parents=True, exist_ok=True)
            name = f"{_stem(shot.taken_at)}.{ext}"
            (folder / name).write_bytes(image)
            meta = {
                **_base_meta(shot),
                "file": name,
                "mime": mime,
                "width": width,
                "height": height,
            }
            if shot.hdr_png:
                hdr_name = f"{_stem(shot.taken_at)}-hdr.png"
                (folder / hdr_name).write_bytes(shot.hdr_png)
                meta["hdr_file"] = hdr_name
            _write_meta(folder, meta)
            _prune()
        return True
    except (OSError, ValueError) as exc:
        log.warning("appshot library: could not keep the appshot (%s)", exc)
        return False


def _png_size(png: bytes) -> tuple[int, int]:
    """Width and height from a PNG's IHDR, without decoding it."""
    import struct  # noqa: PLC0415

    return struct.unpack(">II", png[16:24])


def save_edit(shot: Appshot) -> bool:
    """Keep the user's edit beside its original. ``shot`` carries the edited PNG.

    An appshot that never reached the library (taken while it was off, or a
    turn's own look) gets an entry of its own holding just the edit.
    """
    try:
        folder = _folder(shot.id)
        with _lock:
            folder.mkdir(parents=True, exist_ok=True)
            meta = _read_meta(folder) or _base_meta(shot)
            taken = meta.get("taken_at")
            name = (
                f"{_stem(taken if isinstance(taken, (int, float)) else shot.taken_at)}-edited.png"
            )
            (folder / name).write_bytes(shot.image)
            meta.update(
                {
                    "edited_file": name,
                    "edited_at": time.time(),
                    "edited_width": shot.width,
                    "edited_height": shot.height,
                }
            )
            _write_meta(folder, meta)
            _drop_thumbs(folder, "edited")
            _prune()
        return True
    except (OSError, ValueError) as exc:
        log.warning("appshot library: could not keep the edit (%s)", exc)
        return False


def _items_for(folder: Path, meta: dict[str, object]) -> list[LibraryItem]:
    def _num(key: str, default: float = 0.0) -> float:
        value = meta.get(key, default)
        return float(value) if isinstance(value, (int, float)) else default

    common = {
        "id": folder.name,
        "label": str(meta.get("label") or ""),
        "app_name": str(meta.get("app_name") or ""),
        "trigger": str(meta.get("trigger") or ""),
        "taken_at": _num("taken_at"),
    }
    items: list[LibraryItem] = []
    edited = _edited_file(folder, meta)
    has_edit = edited is not None
    if edited is not None:
        items.append(
            LibraryItem(
                variant="edited",
                path=edited,
                mime="image/png",
                width=int(_num("edited_width")),
                height=int(_num("edited_height")),
                edited_at=_num("edited_at"),
                has_edit=False,
                **common,  # type: ignore[arg-type]
            )
        )
    original = _original_file(folder, meta)
    if original is not None:
        items.append(
            LibraryItem(
                variant="original",
                path=original,
                mime=_MIMES[original.suffix.lstrip(".")],
                width=int(_num("width")),
                height=int(_num("height")),
                edited_at=0.0,
                has_edit=has_edit,
                **common,  # type: ignore[arg-type]
            )
        )
    return items


def list_items() -> list[LibraryItem]:
    """Every kept picture, newest appshot first; an edit right before its original."""
    root = library_root()
    if not root.is_dir():
        return []
    entries: list[tuple[float, list[LibraryItem]]] = []
    with _lock:
        for folder in root.iterdir():
            if not folder.is_dir() or not valid_id(folder.name):
                continue
            meta = _read_meta(folder)
            if meta is None:
                continue
            items = _items_for(folder, meta)
            if items:
                entries.append((items[0].taken_at, items))
    entries.sort(key=lambda entry: entry[0], reverse=True)
    return [item for _taken, items in entries for item in items]


def get_item(shot_id: str, variant: Variant) -> LibraryItem | None:
    if not valid_id(shot_id):
        return None
    folder = library_root() / shot_id
    with _lock:
        meta = _read_meta(folder)
        if meta is None:
            return None
        return next((i for i in _items_for(folder, meta) if i.variant == variant), None)


def thumbnail(item: LibraryItem) -> tuple[bytes, str]:
    """A small JPEG of ``item`` for the gallery grid, made once and kept.

    Falls back to the full picture where Pillow cannot decode it.
    """
    thumb = item.path.parent / f"thumb-{item.variant}-{THUMB_EDGE}.jpg"
    try:
        return thumb.read_bytes(), "image/jpeg"
    except OSError:  # no thumbnail yet: it is made below
        pass
    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(item.path) as image:
            image.thumbnail((THUMB_EDGE, THUMB_EDGE))
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            out = io.BytesIO()
            image.save(out, "JPEG", quality=82)
        data = out.getvalue()
    except (OSError, ValueError, ImportError) as exc:
        log.debug("appshot library: thumbnail failed, serving the picture (%s)", exc)
        return item.path.read_bytes(), item.mime
    try:
        thumb.write_bytes(data)
    except OSError as exc:
        log.debug("appshot library: thumbnail not cached (%s)", exc)
    return data, "image/jpeg"


def load_shot(shot_id: str, variant: Variant) -> Appshot | None:
    """A kept picture as an :class:`Appshot`, to open it in the editor again."""
    item = get_item(shot_id, variant)
    if item is None:
        return None
    try:
        image = item.path.read_bytes()
    except OSError:  # deleted meanwhile: the route answers 404
        return None
    from jarvis.appshot.service import APPSHOT_PREAMBLE  # noqa: PLC0415

    return Appshot(
        id=item.id,
        image=image,
        mime=item.mime,
        width=item.width,
        height=item.height,
        label=item.label or "appshot",
        app_name=item.app_name,
        note=APPSHOT_PREAMBLE,
        ui_text="",
        trigger=item.trigger or "library",
        taken_at=item.taken_at,
    )


def delete(shot_id: str, variant: Variant) -> bool:
    """Remove the edit only, or — for the original — the whole appshot."""
    if not valid_id(shot_id):
        return False
    folder = library_root() / shot_id
    with _lock:
        if not folder.is_dir():
            return False
        meta = _read_meta(folder) or {}
        if variant == "edited" and _original_file(folder, meta) is not None:
            edited = _edited_file(folder, meta)
            if edited is None:
                return False
            try:
                edited.unlink()
            except FileNotFoundError:  # already gone: the route answers 404
                return False
            _drop_thumbs(folder, "edited")
            return True
        shutil.rmtree(folder, ignore_errors=True)
        return not folder.exists()


def clear() -> int:
    """Delete every kept appshot; how many were removed."""
    root = library_root()
    if not root.is_dir():
        return 0
    removed = 0
    with _lock:
        for folder in root.iterdir():
            if folder.is_dir() and valid_id(folder.name):
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
    return removed


def _prune() -> None:
    """Keep at most :data:`MAX_ENTRIES` appshots. Caller holds the lock."""
    root = library_root()
    dated: list[tuple[float, Path]] = []
    for folder in root.iterdir():
        if not folder.is_dir() or not valid_id(folder.name):
            continue
        meta = _read_meta(folder) or {}
        taken = meta.get("taken_at", 0.0)
        dated.append((float(taken) if isinstance(taken, (int, float)) else 0.0, folder))
    if len(dated) <= MAX_ENTRIES:
        return
    dated.sort(key=lambda entry: entry[0])
    for _taken, folder in dated[: len(dated) - MAX_ENTRIES]:
        shutil.rmtree(folder, ignore_errors=True)


__all__ = [
    "MAX_ENTRIES",
    "THUMB_EDGE",
    "LibraryItem",
    "Variant",
    "clear",
    "delete",
    "get_item",
    "library_root",
    "list_items",
    "load_shot",
    "save",
    "save_edit",
    "set_root",
    "thumbnail",
    "valid_id",
]
