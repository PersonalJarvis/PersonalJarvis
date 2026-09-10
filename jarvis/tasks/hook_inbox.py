"""Durable hook deliveries on the existing task database and scheduler."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import aiosqlite

from .schema import HookOptions

MAX_PAYLOAD_BYTES = 32_768
MAX_PENDING = 100
MAX_PER_MINUTE = 60
HISTORY_LIMIT = 4096

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_hook_state (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    last_received_ns INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS task_hook_deliveries (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    delivery_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    lineage_json TEXT NOT NULL DEFAULT '[]',
    received_ns INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','running','done','failed','interrupted')),
    PRIMARY KEY(task_id, delivery_id)
);
CREATE INDEX IF NOT EXISTS idx_task_hooks_pending ON task_hook_deliveries(status,received_ns);
"""


def encode_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Hook payload must be a JSON object")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("Hook payload must contain finite JSON values") from exc
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("Hook payload exceeds 32 KiB")
    return encoded


def matches(payload: dict[str, Any], conditions: dict[str, Any]) -> bool:
    for path, expected in conditions.items():
        value: Any = payload
        for field in path.split("."):
            if not isinstance(value, dict) or field not in value:
                return False
            value = value[field]
        if type(value) is bool or type(expected) is bool:
            if type(value) is not type(expected):
                return False
        if value != expected:
            return False
    return True


class HookInbox:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection
        self._lock = asyncio.Lock()

    async def _one(self, sql: str, args: tuple[Any, ...]) -> Any:
        async with self._conn.execute(sql, args) as cursor:
            return await cursor.fetchone()

    async def accept(
        self, task_id: str, delivery_id: str, payload: dict[str, Any], trigger: HookOptions,
        lineage: tuple[str, ...] = ()
    ) -> str:
        if "task:" + task_id in lineage or len(lineage) >= 16:
            return "cycle"
        encoded = encode_payload(payload)
        async with self._lock:
            existing = await self._one(
                "SELECT payload_json FROM task_hook_deliveries WHERE task_id=? AND delivery_id=?",
                (task_id, delivery_id),
            )
            if existing:
                return "duplicate" if existing[0] == encoded else "id_conflict"
            task = await self._one("SELECT state,spec_json FROM tasks WHERE id=?", (task_id,))
            if task is None or task[0] not in ("scheduled", "running"):
                return "inactive"
            if json.loads(task[1])["trigger"] != trigger.model_dump(mode="json"):
                return "changed"
            if not matches(payload, trigger.conditions):
                return "filtered"
            now = time.time_ns()
            state = await self._one(
                "SELECT accepted_count,last_received_ns FROM task_hook_state WHERE task_id=?",
                (task_id,),
            )
            if state:
                if trigger.max_firings is not None and state[0] >= trigger.max_firings:
                    return "exhausted"
                if now - state[1] < trigger.cooldown_seconds * 1e9:
                    return "cooldown"
            pending = await self._one(
                "SELECT COUNT(*) FROM task_hook_deliveries WHERE task_id=? AND status IN "
                "('pending','running')",
                (task_id,),
            )
            recent = await self._one(
                "SELECT COUNT(*) FROM task_hook_deliveries WHERE task_id=? AND received_ns>?",
                (task_id, now - 60_000_000_000),
            )
            if pending[0] >= MAX_PENDING or recent[0] >= MAX_PER_MINUTE:
                return "rate_limited"
            # One SQL statement records both the delivery and its counter through
            # a trigger, avoiding a shared-connection transaction across awaits.
            inserted = await self._conn.execute(
                "INSERT INTO "
                "task_hook_deliveries(task_id,delivery_id,payload_json,received_ns,lineage_json) "
                "SELECT ?,?,?,?,? FROM tasks WHERE id=? "
                "AND state IN ('scheduled','running') AND spec_json=?",
                (task_id, delivery_id, encoded, now, json.dumps(lineage), task_id, task[1]),
            )
            if inserted.rowcount != 1:
                return "changed"
            await self._conn.execute(
                "DELETE FROM task_hook_deliveries WHERE task_id=? AND status IN "
                "('done','failed','interrupted') "
                "AND delivery_id NOT IN (SELECT delivery_id FROM task_hook_deliveries WHERE "
                "task_id=? ORDER BY received_ns DESC LIMIT ?)",
                (task_id, task_id, HISTORY_LIMIT),
            )
            return "queued"

    async def pending(self) -> list[dict[str, Any]]:
        async with self._conn.execute(
            "SELECT d.* FROM task_hook_deliveries d JOIN tasks t ON t.id=d.task_id "
            "WHERE d.status='pending' AND t.state='scheduled' ORDER BY d.received_ns LIMIT 100"
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def mark(self, task_id: str, delivery_id: str, status: str) -> None:
        await self._conn.execute(
            "UPDATE task_hook_deliveries SET status=? WHERE task_id=? AND delivery_id=?",
            (status, task_id, delivery_id),
        )

    async def counts(self, task_id: str) -> tuple[int, int]:
        row = await self._one(
            "SELECT COALESCE((SELECT accepted_count FROM task_hook_state WHERE task_id=?),0), "
            "(SELECT COUNT(*) FROM task_hook_deliveries WHERE task_id=? AND status IN "
            "('pending','running'))",
            (task_id, task_id),
        )
        return int(row[0]), int(row[1])

    async def recover(self) -> None:
        # Mark even the crash window between claiming a delivery and entering
        # the runner. Never replay a possibly completed external side effect.
        message = "Hook interrupted; inspect its result before retrying"
        await self._conn.execute(
            "UPDATE tasks SET state='scheduled',last_error=?,finished_at_ns=? "
            "WHERE trigger_type IN ('webhook','event_hook','source') "
            "AND state IN ('scheduled','running','interrupted') AND "
            "(state IN ('running','interrupted') OR id IN "
            "(SELECT task_id FROM task_hook_deliveries WHERE status='running'))",
            (message, time.time_ns()),
        )
        await self._conn.execute(
            "UPDATE task_hook_deliveries SET status='interrupted' WHERE status='running'"
        )
        # An exhausted finite routine must not look armed after interruption.
        await self._conn.execute(
            "UPDATE tasks SET state='failed' WHERE state='scheduled' AND last_error=? "
            "AND trigger_type IN ('webhook','event_hook','source') "
            "AND json_extract(spec_json,'$.trigger.max_firings') <= "
            "COALESCE((SELECT accepted_count FROM task_hook_state WHERE task_id=tasks.id),0) "
            "AND NOT EXISTS (SELECT 1 FROM task_hook_deliveries "
            "WHERE task_id=tasks.id AND status='pending')",
            (message,),
        )


COUNTER_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS count_task_hook_delivery AFTER INSERT ON task_hook_deliveries
BEGIN
    INSERT INTO task_hook_state(task_id,accepted_count,last_received_ns)
        VALUES(NEW.task_id,1,NEW.received_ns)
    ON CONFLICT(task_id) DO UPDATE SET accepted_count=accepted_count+1,
                                      last_received_ns=NEW.received_ns;
END;
"""
