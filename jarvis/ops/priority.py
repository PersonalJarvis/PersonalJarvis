"""OpsPriority — the person's own priority and focus marks on WorkLedger items.

This is metadata, not a task list: a row only says "this item (``source`` +
``id`` from the WorkLedger) matters this much to me" and/or "it is my focus
today". It never holds a title, a state or a schedule and it never changes the
mission, task, quest or workflow it points at. A mark whose item no longer
exists stays harmless — the ranking simply finds nothing to apply it to.

Rows are written only by an explicit request from the person (the
``/api/ops/priorities`` routes). Nothing here runs on its own, and an item
without a row has NO priority — the ranking never invents one.

The store keeps its own SQLite file and opens a short connection per call, so
nothing starts on boot and nothing must be closed on shutdown.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from jarvis.ops.ledger import WORK_SOURCES

PriorityLevel = Literal["urgent", "high", "normal", "low"]
PRIORITY_LEVELS: tuple[str, ...] = ("urgent", "high", "normal", "low")

NOTE_MAX = 500
ITEM_ID_MAX = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_priority (
    source      TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    priority    TEXT CHECK (priority IS NULL OR priority IN ('urgent','high','normal','low')),
    focus_date  TEXT,
    note        TEXT NOT NULL DEFAULT '',
    updated_ms  INTEGER NOT NULL,
    PRIMARY KEY (source, item_id)
)
"""


class PriorityError(ValueError):
    """A mark the store refuses (unknown source, level or malformed date)."""


@dataclass(frozen=True, slots=True)
class PriorityMark:
    """The person's mark on one WorkLedger item."""

    source: str
    item_id: str
    priority: str | None = None
    #: Local calendar day (``YYYY-MM-DD``) the item is the focus of. Focus
    #: lapses on its own the next day; nothing is deleted for that.
    focus_date: str | None = None
    note: str = ""
    updated_ms: int = 0

    def focus_on(self, today: date) -> bool:
        return self.focus_date == today.isoformat()

    def to_dict(self, today: date | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "source": self.source,
            "item_id": self.item_id,
            "priority": self.priority,
            "focus_date": self.focus_date,
            "note": self.note,
            "updated_ms": self.updated_ms,
        }
        if today is not None:
            data["focus_today"] = self.focus_on(today)
        return data


def validate_mark(mark: PriorityMark) -> PriorityMark:
    if mark.source not in WORK_SOURCES:
        raise PriorityError(f"unknown source {mark.source!r}")
    if not mark.item_id or len(mark.item_id) > ITEM_ID_MAX:
        raise PriorityError("item_id must be 1-200 characters")
    if mark.priority is not None and mark.priority not in PRIORITY_LEVELS:
        raise PriorityError(f"unknown priority {mark.priority!r}")
    if mark.focus_date is not None:
        try:
            date.fromisoformat(mark.focus_date)
        except ValueError as exc:
            raise PriorityError("focus_date must be YYYY-MM-DD") from exc
    if len(mark.note) > NOTE_MAX:
        raise PriorityError(f"note longer than {NOTE_MAX} characters")
    return mark


class OpsPriorityStore:
    """CRUD for priority marks in their own SQLite file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def _connect(self) -> aiosqlite.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout = 5000")
        await conn.execute(_SCHEMA)
        return conn

    async def all(self) -> list[PriorityMark]:
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT source, item_id, priority, focus_date, note, updated_ms "
                "FROM ops_priority ORDER BY updated_ms DESC"
            )
            rows = await cur.fetchall()
        finally:
            await conn.close()
        return [_mark(row) for row in rows]

    async def get(self, source: str, item_id: str) -> PriorityMark | None:
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "SELECT source, item_id, priority, focus_date, note, updated_ms "
                "FROM ops_priority WHERE source = ? AND item_id = ?",
                (source, item_id),
            )
            row = await cur.fetchone()
        finally:
            await conn.close()
        return _mark(row) if row is not None else None

    async def put(self, mark: PriorityMark) -> PriorityMark:
        """Store the person's mark. A mark with neither priority, focus nor
        note says nothing, so it removes the row instead."""
        mark = validate_mark(mark)
        if mark.priority is None and mark.focus_date is None and not mark.note:
            await self.delete(mark.source, mark.item_id)
            return PriorityMark(mark.source, mark.item_id)
        stored = PriorityMark(
            mark.source,
            mark.item_id,
            mark.priority,
            mark.focus_date,
            mark.note,
            int(time.time() * 1000),
        )
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT INTO ops_priority (source, item_id, priority, focus_date, note, updated_ms)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (source, item_id) DO UPDATE SET priority = excluded.priority,"
                " focus_date = excluded.focus_date, note = excluded.note,"
                " updated_ms = excluded.updated_ms",
                (
                    stored.source,
                    stored.item_id,
                    stored.priority,
                    stored.focus_date,
                    stored.note,
                    stored.updated_ms,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
        return stored

    async def delete(self, source: str, item_id: str) -> bool:
        conn = await self._connect()
        try:
            cur = await conn.execute(
                "DELETE FROM ops_priority WHERE source = ? AND item_id = ?", (source, item_id)
            )
            await conn.commit()
            return (cur.rowcount or 0) > 0
        finally:
            await conn.close()


def _mark(row: Any) -> PriorityMark:
    return PriorityMark(
        source=str(row["source"]),
        item_id=str(row["item_id"]),
        priority=row["priority"],
        focus_date=row["focus_date"],
        note=str(row["note"] or ""),
        updated_ms=int(row["updated_ms"] or 0),
    )


__all__ = [
    "ITEM_ID_MAX",
    "NOTE_MAX",
    "PRIORITY_LEVELS",
    "OpsPriorityStore",
    "PriorityError",
    "PriorityLevel",
    "PriorityMark",
    "validate_mark",
]
