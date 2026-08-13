"""ErrandStore — durable persistence for errands (C1: survives a restart).

Deliberately dumb, exactly like ``jarvis.tasks.store``: CRUD and nothing else.
No scheduling, no brain, no event publishing — the runner owns all of that.

Pattern matches ``jarvis/tasks/store.py`` and ``jarvis/memory/recall.py``:
aiosqlite plus a lazy ``_ensure_open`` so the store can be constructed
synchronously and opened on first use.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

from .schema import TERMINAL_STATES, Errand, ErrandState

log = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


class ErrandStore:
    """SQLite-backed record store for errands."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def _ensure_open(self) -> aiosqlite.Connection:
        if self._db is not None:
            return self._db
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
        await self._db.commit()
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def save(self, errand: Errand) -> None:
        """Insert or update. Called after EVERY leg — the restart guarantee is
        only as good as the last write."""
        db = await self._ensure_open()
        await db.execute(
            """
            INSERT INTO errands (id, goal, state, record_json, trace_id,
                                 created_at_ns, updated_at_ns)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                goal          = excluded.goal,
                state         = excluded.state,
                record_json   = excluded.record_json,
                updated_at_ns = excluded.updated_at_ns
            """,
            (
                errand.id,
                errand.goal,
                str(errand.state),
                errand.model_dump_json(),
                errand.trace_id,
                errand.created_at_ns,
                errand.updated_at_ns,
            ),
        )
        await db.commit()

    async def get(self, errand_id: str) -> Errand | None:
        db = await self._ensure_open()
        async with db.execute(
            "SELECT record_json FROM errands WHERE id = ?", (errand_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._decode(row["record_json"])

    async def list_open(self) -> list[Errand]:
        """Every errand still capable of moving, newest first.

        This is the resume query (C1). A restarted process picks these up
        rather than reporting them failed.
        """
        db = await self._ensure_open()
        open_states = [str(s) for s in ErrandState if s not in TERMINAL_STATES]
        placeholders = ",".join("?" * len(open_states))
        async with db.execute(
            f"SELECT record_json FROM errands WHERE state IN ({placeholders})"  # noqa: S608 — placeholders only
            " ORDER BY updated_at_ns DESC",
            open_states,
        ) as cursor:
            rows = await cursor.fetchall()
        return [e for e in (self._decode(r["record_json"]) for r in rows) if e]

    async def list_recent(self, limit: int = 20) -> list[Errand]:
        db = await self._ensure_open()
        async with db.execute(
            "SELECT record_json FROM errands ORDER BY updated_at_ns DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [e for e in (self._decode(r["record_json"]) for r in rows) if e]

    @staticmethod
    def _decode(raw: str) -> Errand | None:
        """Parse one row, tolerating a record written by an older schema.

        A single unreadable row must never take down the resume path — the
        other errands still deserve to continue.
        """
        try:
            return Errand.model_validate_json(raw)
        except Exception:  # noqa: BLE001 — one bad row is not a fatal error
            log.warning("errand store: skipping unreadable record", exc_info=True)
            return None


__all__ = ["ErrandStore"]
