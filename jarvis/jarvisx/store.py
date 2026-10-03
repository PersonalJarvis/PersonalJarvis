"""The Jarvis X library index — one SQLite table of captures.

The pictures and videos themselves live in the user's save folder under their
readable names; the index remembers what each file is (kind, mode, size,
duration), where its preview thumbnail sits, and whether an annotated copy
exists. A file deleted outside the app drops out of the index on the next
listing, so the library never shows a tile that cannot load.

Stdlib only. Every call opens its own short-lived connection under one lock:
the store is touched from worker threads (``asyncio.to_thread``) and the
recorder's finishing thread, never from the event loop itself.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

Kind = Literal["image", "video"]
Mode = Literal["region", "window", "fullscreen"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    duration_s REAL,
    path TEXT NOT NULL,
    thumb_path TEXT NOT NULL DEFAULT '',
    edited_path TEXT
);
"""

_COLUMNS = "id, kind, mode, created_at, width, height, duration_s, path, thumb_path, edited_path"
_INSERT = f"INSERT OR REPLACE INTO items ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?)"  # noqa: S608
_SELECT_ONE = f"SELECT {_COLUMNS} FROM items WHERE id = ?"  # noqa: S608
_SELECT_RECENT = f"SELECT {_COLUMNS} FROM items ORDER BY rowid DESC LIMIT ?"  # noqa: S608


@dataclass(frozen=True, slots=True)
class Item:
    """One capture in the library."""

    id: str
    kind: Kind
    mode: Mode
    #: ISO-8601 with the local UTC offset, e.g. ``2026-09-29T14:03:22+02:00``.
    created_at: str
    width: int
    height: int
    duration_s: float | None
    path: str
    thumb_path: str = ""
    edited_path: str | None = None

    @property
    def filename(self) -> str:
        return Path(self.path).name

    def has_edited(self) -> bool:
        return bool(self.edited_path) and Path(str(self.edited_path)).is_file()

    def to_json(self) -> dict[str, Any]:
        """The REST shape (``/api/jarvisx/items``)."""
        base = f"/api/jarvisx/items/{self.id}"
        return {
            "id": self.id,
            "kind": self.kind,
            "mode": self.mode,
            "created_at": self.created_at,
            "width": int(self.width),
            "height": int(self.height),
            "duration_s": None if self.duration_s is None else round(float(self.duration_s), 2),
            "filename": self.filename,
            "path": self.path,
            "url": f"{base}/file",
            "thumb_url": f"{base}/thumb",
            "edited_url": f"{base}/edited" if self.has_edited() else None,
        }


class ItemStore:
    """SQLite-backed index. ``root`` holds ``index.sqlite3`` and ``thumbs/``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()
        self._ready = False

    @property
    def db_path(self) -> Path:
        return self.root / "index.sqlite3"

    @property
    def thumbs_dir(self) -> Path:
        return self.root / "thumbs"

    def thumb_path_for(self, item_id: str) -> Path:
        return self.thumbs_dir / f"{item_id}.jpg"

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if not self._ready:
                self.root.mkdir(parents=True, exist_ok=True)
                self.thumbs_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            try:
                if not self._ready:
                    conn.executescript(_SCHEMA)
                    self._ready = True
                yield conn
                conn.commit()
            finally:
                conn.close()

    # ----------------------------------------------------------------- write
    def add(self, item: Item) -> Item:
        with self._connect() as conn:
            conn.execute(
                _INSERT,
                (
                    item.id,
                    item.kind,
                    item.mode,
                    item.created_at,
                    int(item.width),
                    int(item.height),
                    item.duration_s,
                    item.path,
                    item.thumb_path,
                    item.edited_path,
                ),
            )
        return item

    def set_edited(self, item_id: str, edited_path: str) -> Item | None:
        with self._connect() as conn:
            conn.execute("UPDATE items SET edited_path = ? WHERE id = ?", (edited_path, item_id))
        return self.get(item_id)

    def delete(self, item_id: str) -> Item | None:
        """Remove the row and every file it names. Returns the removed item."""
        item = self.get(item_id)
        if item is None:
            return None
        for name in (item.path, item.thumb_path, item.edited_path):
            if not name:
                continue
            try:
                Path(name).unlink(missing_ok=True)
            except OSError:
                # The row still goes: a locked file must not pin a dead tile.
                log.warning("jarvisx: could not delete %s", name, exc_info=True)
        with self._connect() as conn:
            conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        return item

    # ------------------------------------------------------------------ read
    def get(self, item_id: str) -> Item | None:
        with self._connect() as conn:
            row = conn.execute(_SELECT_ONE, (item_id,)).fetchone()
        return _row_to_item(row) if row else None

    def recent(self, limit: int = 100) -> list[Item]:
        """Newest first. Rows whose file vanished are pruned on the way."""
        limit = max(1, min(int(limit), 1000))
        with self._connect() as conn:
            rows = conn.execute(_SELECT_RECENT, (limit,)).fetchall()
        items: list[Item] = []
        gone: list[Item] = []
        for row in rows:
            item = _row_to_item(row)
            (items if Path(item.path).is_file() else gone).append(item)
        for item in gone:
            log.info("jarvisx: %s was removed outside the app; dropping it", item.filename)
            self.delete(item.id)
        return items


def _row_to_item(row: tuple[Any, ...]) -> Item:
    kind = "video" if row[1] == "video" else "image"
    mode = row[2] if row[2] in ("region", "window", "fullscreen") else "region"
    return Item(
        id=str(row[0]),
        kind=kind,
        mode=mode,
        created_at=str(row[3]),
        width=int(row[4]),
        height=int(row[5]),
        duration_s=None if row[6] is None else float(row[6]),
        path=str(row[7]),
        thumb_path=str(row[8] or ""),
        edited_path=str(row[9]) if row[9] else None,
    )


_store: ItemStore | None = None
_store_lock = threading.Lock()


def get_store() -> ItemStore:
    """The process-wide store under ``<data dir>/jarvisx``."""
    global _store
    with _store_lock:
        if _store is None:
            from jarvis.jarvisx.paths import library_dir  # noqa: PLC0415

            _store = ItemStore(library_dir())
        return _store


def set_store_for_tests(store: ItemStore | None) -> None:
    global _store
    with _store_lock:
        _store = store


__all__ = ["Item", "ItemStore", "Kind", "Mode", "get_store", "set_store_for_tests"]
