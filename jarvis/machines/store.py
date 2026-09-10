"""Durable device trust, per-agent grants and at-most-once dispatch ledger."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from uuid import uuid4

import aiosqlite

from .models import MachineCapabilities, MachineGrant

SCHEMA = """
CREATE TABLE IF NOT EXISTS machines (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, transport TEXT NOT NULL
 CHECK(transport IN ('connector','ssh')), token_hash TEXT UNIQUE,
 capabilities TEXT NOT NULL DEFAULT '{}', revoked INTEGER NOT NULL DEFAULT 0,
 last_seen REAL NOT NULL DEFAULT 0, settings TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS pairing (
 token_hash TEXT PRIMARY KEY, name TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS grants (
 agent_id TEXT NOT NULL, machine_id TEXT NOT NULL, body TEXT NOT NULL,
 PRIMARY KEY(agent_id,machine_id));
CREATE TABLE IF NOT EXISTS placements (
 agent_id TEXT PRIMARY KEY, host_id TEXT NOT NULL DEFAULT 'local',
 generation INTEGER NOT NULL DEFAULT 0, moving INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS machine_jobs (
 id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, machine_id TEXT NOT NULL,
 trace_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN
 ('queued','running','succeeded','failed','uncertain','cancelled')),
 command TEXT NOT NULL, result TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS transfers (
 id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, source TEXT NOT NULL,
 target TEXT NOT NULL, state TEXT NOT NULL, manifest TEXT NOT NULL DEFAULT '{}');
"""


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class MachineStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def connect(self) -> aiosqlite.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.path, timeout=10)
        db.row_factory = aiosqlite.Row
        return db

    async def initialize(self) -> None:
        db = await self.connect()
        try:
            await db.executescript(SCHEMA)
            # A lost supervisor cannot prove whether a dispatched action finished.
            await db.execute("UPDATE machine_jobs SET state='uncertain' WHERE state='running'")
            await db.execute(
                "UPDATE transfers SET state='interrupted' WHERE state IN ('preparing','queued')"
            )
            await db.execute(
                "UPDATE placements SET moving=0 WHERE moving=1 AND agent_id IN "
                "(SELECT agent_id FROM transfers WHERE state='interrupted' "
                "AND source=placements.host_id)"
            )
            await db.commit()
        finally:
            await db.close()

    async def rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        db = await self.connect()
        try:
            cursor = await db.execute(sql, args)
            return [dict(row) for row in await cursor.fetchall()]
        finally:
            await db.close()

    async def write(self, sql: str, args: tuple[Any, ...] = ()) -> int:
        db = await self.connect()
        try:
            cursor = await db.execute(sql, args)
            await db.commit()
            return cursor.rowcount
        finally:
            await db.close()

    async def pair_code(self, name: str) -> str:
        token = secrets.token_urlsafe(32)
        await self.write("DELETE FROM pairing WHERE expires < ?", (time.time(),))
        await self.write(
            "INSERT INTO pairing VALUES (?,?,?)", (digest(token), name, time.time() + 600)
        )
        return token

    async def reserve_transfer(
        self, agent_id: str, target: str, transfer_id: str
    ) -> dict[str, Any]:
        """Atomically record the source owner and pause new work under its generation."""
        db = await self.connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("INSERT OR IGNORE INTO placements(agent_id) VALUES (?)", (agent_id,))
            cursor = await db.execute("SELECT * FROM placements WHERE agent_id=?", (agent_id,))
            row = await cursor.fetchone()
            assert row is not None
            old = dict(row)
            if old["moving"]:
                raise PermissionError("Agent already has an unfinished transfer")
            await db.execute("UPDATE placements SET moving=1 WHERE agent_id=?", (agent_id,))
            await db.execute(
                "INSERT INTO transfers(id,agent_id,source,target,state) VALUES (?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET source=excluded.source,state=excluded.state",
                (transfer_id, agent_id, old["host_id"], target, "preparing"),
            )
            await db.commit()
            return old
        finally:
            await db.close()

    async def enroll(self, code: str, capabilities: MachineCapabilities) -> tuple[str, str]:
        db = await self.connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "DELETE FROM pairing WHERE token_hash=? AND expires>? RETURNING name",
                (digest(code), time.time()),
            )
            row = await cursor.fetchone()
            if row is None:
                raise PermissionError("Pairing code is invalid, expired or already used")
            machine_id, token = uuid4().hex, secrets.token_urlsafe(48)
            await db.execute(
                "INSERT INTO machines(id,name,transport,token_hash,capabilities) "
                "VALUES (?,?,?,?,?)",
                (machine_id, row[0], "connector", digest(token), capabilities.model_dump_json()),
            )
            await db.commit()
            return machine_id, token
        finally:
            await db.close()

    async def authenticate(self, token: str) -> str | None:
        rows = await self.rows(
            "SELECT id FROM machines WHERE token_hash=? AND revoked=0 AND transport='connector'",
            (digest(token),),
        )
        return rows[0]["id"] if rows else None

    async def machine(self, machine_id: str) -> dict[str, Any]:
        rows = await self.rows("SELECT * FROM machines WHERE id=? AND revoked=0", (machine_id,))
        if not rows:
            raise PermissionError("Machine is unknown or revoked")
        row = rows[0]
        row.pop("token_hash", None)
        row["capabilities"] = json.loads(row["capabilities"])
        row["settings"] = json.loads(row["settings"])
        return row

    async def grant(self, agent_id: str, machine_id: str) -> MachineGrant:
        rows = await self.rows(
            "SELECT body FROM grants WHERE agent_id=? AND machine_id=?", (agent_id, machine_id)
        )
        if not rows:
            raise PermissionError("This agent has no permission on this machine")
        return MachineGrant.model_validate_json(rows[0]["body"])

    async def put_grant(self, grant: MachineGrant) -> None:
        machine = await self.machine(grant.machine_id)
        path_class = (
            PureWindowsPath if machine["capabilities"]["os"] == "windows" else PurePosixPath
        )
        if not path_class(grant.workspace).is_absolute():
            raise PermissionError("Enter an absolute workspace path on the target computer")
        await self.write(
            "INSERT INTO grants VALUES (?,?,?) ON CONFLICT(agent_id,machine_id) "
            "DO UPDATE SET body=excluded.body",
            (grant.agent_id, grant.machine_id, grant.model_dump_json()),
        )
