"""Expiring Redis presence/coordination observations, revalidated against PostgreSQL."""

from __future__ import annotations

import json
import logging
import math
import threading
from collections import OrderedDict
from typing import Any

from jarvis.swarm.store import _digest, _json

from .database import team_schema

log = logging.getLogger(__name__)
TTL_SECONDS = 60
PAGE_SIZE = 32


def _report(connection: Any, agent_id: str, request_id: str | None = None) -> dict[str, Any] | None:
    if request_id:
        row = connection.execute(
            "SELECT record FROM decisions WHERE id=?",
            (request_id,),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT record FROM decisions WHERE json_extract(record,'$.kind')='control_request' "
            "AND json_extract(record,'$.actor_id')=? "
            "AND json_extract(record,'$.operation')='report_summary' "
            "AND json_extract(record,'$.state')='applied' ORDER BY rowid DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
    value = json.loads(row[0]) if row else None
    if (
        not value
        or value.get("actor_id") != agent_id
        or value.get("actor_role") != "coordinator"
        or value.get("operation") != "report_summary"
        or value.get("state") != "applied"
    ):
        return None
    return {
        "id": value["id"],
        "task_ids": value["payload"]["task_ids"][:32],
        "evidence": value["payload"].get("evidence", [])[:32],
        "reported_at": value["created_at"],
    }


class RedisCoordinationCache:
    """Publish/read Redis only on maintenance; discovery uses a bounded local observation map.

    PG still authenticates the caller and resolves every report reference. Redis
    outages clear observations; scoped PG lookup remains a complete fallback.
    """

    def __init__(self, delivery: Any):
        self.delivery = delivery
        self._lock = threading.Lock()
        self._observations: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
        self._cursors: OrderedDict[str, str] = OrderedDict()

    def key(self, team_id: str, agent_id: str) -> str:
        team_schema(self.delivery.config.namespace, team_id)
        return f"{self.delivery.config.namespace}:{{{team_id}}}:presence:{_digest(agent_id)}"

    def refresh(self, store: Any) -> int:
        with self._lock:
            after = self._cursors.get(store.team_id, "")
        snapshots = []
        with store._tx() as connection:
            store._team(connection)
            rows = connection.execute(
                "SELECT record FROM agents WHERE active=1 AND id>? ORDER BY id LIMIT ?",
                (after, PAGE_SIZE),
            ).fetchall()
            for row in rows:
                agent = json.loads(row[0])
                heartbeat = connection.execute(
                    "SELECT heartbeat_at FROM attempts WHERE agent_id=? AND state='running' "
                    "ORDER BY started_at DESC LIMIT 1",
                    (agent["id"],),
                ).fetchone()
                report = (
                    _report(connection, agent["id"]) if agent["role"] == "coordinator" else None
                )
                snapshots.append(
                    {
                        "team_id": store.team_id,
                        "agent_id": agent["id"],
                        "role": agent["role"],
                        "group_id": agent["group_id"],
                        "generation": agent["generation"],
                        "state": agent["state"],
                        "task_id": agent["task_id"],
                        "heartbeat_at": heartbeat[0] if heartbeat else None,
                        "report_id": report["id"] if report else None,
                        "observed_at": store.clock(),
                        "expires_at": store.clock() + TTL_SECONDS,
                    }
                )
        # No PG transaction or authority lock may cross this network boundary.
        try:
            with self.delivery.client().pipeline(transaction=False) as pipeline:
                for snapshot in snapshots:
                    key = self.key(store.team_id, snapshot["agent_id"])
                    pipeline.set(key, _json(snapshot), ex=TTL_SECONDS)
                    pipeline.get(key)
                results = pipeline.execute()
        except Exception:  # noqa: BLE001 - cache failure never changes authority or inferred presence
            self.discard(store.team_id)
            log.info("Redis coordination observations unavailable; using PostgreSQL", exc_info=True)
            return 0
        observed = []
        for raw in results[1::2]:
            if not isinstance(raw, (str, bytes)) or len(raw) > 4096:
                continue
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError):
                log.info("Ignoring malformed Redis coordination observation")
                continue
            if isinstance(value, dict) and value.get("team_id") == store.team_id:
                agent_id = value.get("agent_id")
                if isinstance(agent_id, str) and any(
                    item["agent_id"] == agent_id for item in snapshots
                ):
                    observed.append(value)
        with self._lock:
            self._cursors[store.team_id] = (
                snapshots[-1]["agent_id"] if len(snapshots) == PAGE_SIZE else ""
            )
            self._cursors.move_to_end(store.team_id)
            while len(self._cursors) > 256:
                self._cursors.popitem(last=False)
            for value in observed:
                key = (store.team_id, value["agent_id"])
                self._observations[key] = value
                self._observations.move_to_end(key)
            while len(self._observations) > 2048:
                self._observations.popitem(last=False)
        return len(observed)

    def discard(self, team_id: str) -> None:
        with self._lock:
            self._cursors.pop(team_id, None)
            for key in list(self._observations):
                if key[0] == team_id:
                    self._observations.pop(key, None)

    def enrich(self, store: Any, actor: Any, peers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Authorize and resolve optional hints without Redis I/O on an execution lane."""
        now = store.clock()
        result = []
        with self._lock:
            observations = {
                peer["id"]: dict(self._observations.get((store.team_id, peer["id"]), {}))
                for peer in peers
            }
        with store._tx() as connection:
            store._actor(connection, actor)
            for peer in peers:
                row = connection.execute(
                    "SELECT record FROM agents WHERE id=? AND active=1",
                    (peer["id"],),
                ).fetchone()
                if row is None:
                    continue
                current = json.loads(row[0])
                cached = observations[peer["id"]]
                observed = cached.get("observed_at")
                valid = (
                    type(observed) in {float, int}
                    and math.isfinite(observed)
                    and now - TTL_SECONDS <= observed <= now
                    and cached.get("team_id") == store.team_id
                    and cached.get("agent_id") == current["id"]
                    and all(
                        cached.get(key) == current[key]
                        for key in ("generation", "role", "group_id", "state", "task_id")
                    )
                )
                report_id = cached.get("report_id") if valid else None
                if not isinstance(report_id, str) or len(report_id) > 100:
                    report_id = None
                report = (
                    _report(connection, current["id"], report_id)
                    if current["role"] == "coordinator"
                    else None
                )
                if report_id and report is None:
                    valid = False
                    report = _report(connection, current["id"])
                heartbeat = connection.execute(
                    "SELECT heartbeat_at FROM attempts WHERE agent_id=? AND state='running' "
                    "ORDER BY started_at DESC LIMIT 1",
                    (current["id"],),
                ).fetchone()
                result.append(
                    dict(
                        current,
                        coordination_observation={
                            "source": "redis" if valid else "postgresql",
                            "authority": "hint",
                        "observed_at": observed if valid else now,
                        "expires_at": observed + TTL_SECONDS if valid else None,
                            "heartbeat_at": heartbeat[0] if heartbeat else None,
                            "observed_report": report,
                        },
                    )
                )
        return result
