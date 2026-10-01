"""Small opt-in proposal inbox; requesting a team never starts execution."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from jarvis.core.swarm_types import SwarmBrief

from .store import SwarmAccessError, SwarmConflictError


class RequestInbox:
    def __init__(self, root: Path):
        self.path = root / "requests.sqlite3"

    @contextmanager
    def _tx(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS requests "
                "(id TEXT PRIMARY KEY, source TEXT NOT NULL, op_key TEXT UNIQUE NOT NULL, "
                "state TEXT NOT NULL, created REAL NOT NULL, record TEXT NOT NULL)"
            )
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def enqueue(self, profile: dict[str, Any], brief: SwarmBrief) -> dict[str, Any]:
        source = profile["id"]
        key = hashlib.sha256((source + ":" + brief.request_key).encode()).hexdigest()
        payload = brief.model_dump(mode="json")
        with self._tx() as connection:
            old = connection.execute(
                "SELECT record FROM requests WHERE op_key=?", (key,)
            ).fetchone()
            if old:
                record = json.loads(old[0])
                if record["brief"] != payload:
                    raise SwarmConflictError("Request key identifies a different Swarm proposal")
                return record
            pending = connection.execute(
                "SELECT count(*) FROM requests WHERE source=? AND state='pending'", (source,)
            ).fetchone()[0]
            recent = connection.execute(
                "SELECT count(*) FROM requests WHERE source=? AND created>?",
                (source, time.time() - 3600),
            ).fetchone()[0]
            total = connection.execute("SELECT count(*) FROM requests").fetchone()[0]
            if pending >= 10 or recent >= 20 or total >= 1000:
                raise SwarmConflictError("Swarm request inbox limit reached")
            record = {
                "id": uuid4().hex,
                "source_agent_id": source,
                "source_name": profile["name"],
                "state": "pending",
                "created_at": time.time(),
                "brief": payload,
                "team_id": None,
            }
            connection.execute(
                "INSERT INTO requests VALUES (?,?,?,'pending',?,?)",
                (record["id"], source, key, record["created_at"], json.dumps(record)),
            )
            return record

    def get(self, request_id: str, source: str | None = None) -> dict[str, Any]:
        if not self.path.is_file():
            raise SwarmAccessError("Swarm request unavailable")
        with self._tx() as connection:
            row = connection.execute(
                "SELECT record FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            record = json.loads(row[0]) if row else None
            if not record or (source is not None and source != record["source_agent_id"]):
                raise SwarmAccessError("Swarm request unavailable")
            return record

    def list(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("Invalid request page")
        if not self.path.is_file():
            return []
        with self._tx() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT record FROM requests ORDER BY created DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def decide(
        self,
        request_id: str,
        state: str,
        *,
        team_id: str | None = None,
        approval: str = "",
        spec: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if state not in {"approving", "approved", "rejected"}:
            raise ValueError("Invalid request decision")
        with self._tx() as connection:
            row = connection.execute(
                "SELECT record FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise SwarmAccessError("Swarm request unavailable")
            record = json.loads(row[0])
            if record["state"] in {"approved", "rejected"}:
                if record["state"] != state or record["team_id"] != team_id:
                    raise SwarmConflictError("Swarm request was already decided")
                return record
            if record.get("approval") and record["approval"] != approval:
                raise SwarmConflictError("Approval retry must retain the authorized team settings")
            if record["state"] == "approving" and state == "rejected":
                raise SwarmConflictError("Recover the in-progress approval before rejecting it")
            record.update(state=state, team_id=team_id, approval=approval)
            if spec is not None:
                record["approval_spec"] = spec
            connection.execute(
                "UPDATE requests SET state=?,record=? WHERE id=?",
                (state, json.dumps(record), request_id),
            )
            return record
