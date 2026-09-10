"""Persistent checkpoints and public connection state for trigger listeners."""

import json
import time
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_source_state (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
 status TEXT NOT NULL DEFAULT 'idle', detail TEXT NOT NULL DEFAULT '',
 cursor_json TEXT NOT NULL DEFAULT '{}', updated_ns INTEGER NOT NULL DEFAULT 0
);
"""


class SourceStore:
    def __init__(self, connection: aiosqlite.Connection):
        self.connection = connection

    async def read(self, task_id: str) -> dict[str, Any]:
        async with self.connection.execute(
            "SELECT * FROM task_source_state WHERE task_id=?", (task_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else {"status": "idle", "detail": "", "cursor_json": "{}"}

    async def status(self, task_id: str, status: str, detail: str = "") -> None:
        await self.connection.execute(
            "INSERT INTO task_source_state(task_id,status,detail,updated_ns) SELECT id,?,?,? "
            "FROM tasks WHERE id=? "
            "ON CONFLICT(task_id) DO UPDATE SET "
            "status=excluded.status,detail=excluded.detail,updated_ns=excluded.updated_ns",
            (status, detail, time.time_ns(), task_id),
        )

    async def checkpoint(self, task_id: str, value: dict[str, Any]) -> None:
        await self.connection.execute(
            "UPDATE task_source_state SET cursor_json=?,updated_ns=? WHERE task_id=?",
            (json.dumps(value, ensure_ascii=False), time.time_ns(), task_id),
        )
