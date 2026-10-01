"""Durable bounded peer delivery. Message text never acquires scheduler authority."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from jarvis.core.swarm_types import PeerMessage, SwarmActor

if TYPE_CHECKING:
    from jarvis.swarm.store import TeamStore


class MailboxMixin:
    def send_message(self, actor: SwarmActor, message: PeerMessage) -> dict[str, Any]:
        from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, _id, _json

        store = cast("TeamStore", self)
        message = PeerMessage.model_validate(message)
        with store._tx(write=True) as connection:
            store._actor(connection, actor, writing=True)
            store._require_tool(connection, "send_message")
            store._task(connection, message.task_id)
            existing = connection.execute(
                "SELECT record FROM messages WHERE sender_id=? AND request_key=?",
                (actor.agent_id, message.request_key),
            ).fetchone()
            if existing:
                record = json.loads(existing[0])
                if any(
                    record[key] != value for key, value in message.model_dump(mode="json").items()
                ):
                    raise SwarmConflictError("Message key already identifies different content")
                return record
            now = store.clock()
            if message.deadline is not None and message.deadline <= now:
                raise ValueError("Cannot send an already expired message")
            sent = connection.execute(
                "SELECT count(*) FROM messages WHERE sender_id=? AND created_at>?",
                (actor.agent_id, now - 60),
            ).fetchone()[0]
            if sent >= 30:
                raise SwarmConflictError(
                    "Peer producer rate exceeded; retry after the current minute"
                )
            recipients = set(message.recipients)
            if message.topic:
                recipients.update(
                    row[0]
                    for row in connection.execute(
                        "SELECT s.agent_id FROM subscriptions s JOIN agents a ON "
                        "a.id=s.agent_id WHERE s.topic=? AND (s.task_id='' OR s.task_id=?) "
                        "AND a.active=1 ORDER BY s.agent_id LIMIT 17",
                        (message.topic, message.task_id),
                    )
                )
            if not recipients:
                recipients.add(store._team(connection)["lead_id"])
            if len(recipients) > 16:
                raise SwarmConflictError(
                    "Topic fan-out exceeds 16; route through a scoped coordinator"
                )
            for recipient in recipients:
                if not connection.execute(
                    "SELECT 1 FROM agents WHERE id=? AND active=1", (recipient,)
                ).fetchone():
                    raise SwarmAccessError("Recipient is not an active member of this team")
                depth = connection.execute(
                    "SELECT count(*) FROM deliveries d JOIN messages m ON m.id=d.message_id "
                    "WHERE d.recipient_id=? AND d.acknowledged_at IS NULL AND m.expires_at>?",
                    (recipient, now),
                ).fetchone()[0]
                if depth >= 200:
                    raise SwarmConflictError("Recipient mailbox is full; retry after consumption")
            for reference in message.evidence:
                if not connection.execute(
                    "SELECT 1 FROM artifacts WHERE id=? UNION ALL SELECT 1 FROM messages "
                    "WHERE id=?",
                    (reference, reference),
                ).fetchone():
                    raise SwarmAccessError(
                        "Evidence must reference an existing team artifact or message"
                    )
            record = dict(
                message.model_dump(mode="json"),
                id=_id(),
                team_id=store.team_id,
                sender_id=actor.agent_id,
                source_task_id=actor.task_id or message.task_id,
                created_at=now,
                expires_at=min(message.deadline or now + 86400 * 7, now + 86400 * 7),
                authority="peer_data",
            )
            if len(_json(record).encode("utf-8")) > 12_288:
                raise ValueError("Peer message exceeds 12 KiB")
            connection.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    record["id"],
                    actor.agent_id,
                    message.task_id,
                    message.request_key,
                    message.intent.value,
                    message.topic,
                    message.priority,
                    now,
                    record["expires_at"],
                    _json(record),
                ),
            )
            connection.executemany(
                "INSERT INTO deliveries (message_id,recipient_id) VALUES (?,?)",
                [(record["id"], recipient) for recipient in sorted(recipients)],
            )
            store._event(
                connection,
                "peer." + message.intent.value.lower(),
                message.summary,
                agent_id=actor.agent_id,
                task_id=message.task_id,
                data={
                    "message_id": record["id"],
                    "recipients": sorted(recipients),
                    "evidence": message.evidence,
                },
            )
            return record

    def messages(
        self, actor: SwarmActor, *, limit: int = 20, include_acked: bool = False
    ) -> list[dict[str, Any]]:
        """Claim a delivery batch for 30 seconds; unacknowledged work redelivers.

        Rank round-robin by sender before priority, so one high-priority producer
        cannot consume every slot. ACK is separate from business transitions.
        """
        from jarvis.swarm.store import _page

        store = cast("TeamStore", self)
        _page(limit)
        with store._tx(write=True) as connection:
            store._actor(connection, actor)
            store._require_tool(connection, "read_messages")
            now = store.clock()
            rows = connection.execute(
                "SELECT * FROM (SELECT m.record,m.id,m.priority,m.created_at,d.deliveries, "
                "row_number() OVER (PARTITION BY m.sender_id ORDER BY m.priority "
                "DESC,m.created_at,m.id) AS sender_rank "
                "FROM deliveries d JOIN messages m ON m.id=d.message_id WHERE d.recipient_id=? "
                "AND (? OR d.acknowledged_at IS NULL) AND d.lease_until<=? AND m.expires_at>?) "
                "ORDER BY sender_rank,priority DESC,created_at,id LIMIT ?",
                (actor.agent_id, int(include_acked), now, now, limit),
            ).fetchall()
            result = []
            for row in rows:
                connection.execute(
                    "UPDATE deliveries SET lease_until=?,deliveries=deliveries+1 WHERE "
                    "message_id=? AND recipient_id=?",
                    (now + 30, row["id"], actor.agent_id),
                )
                result.append(dict(json.loads(row["record"]), delivery_count=row["deliveries"] + 1))
            return result

    def ack_message(self, actor: SwarmActor, message_id: str) -> bool:
        from jarvis.swarm.store import SwarmAccessError

        store = cast("TeamStore", self)
        with store._tx(write=True) as connection:
            store._actor(connection, actor)
            store._require_tool(connection, "ack_message")
            row = connection.execute(
                "SELECT acknowledged_at,deliveries FROM deliveries WHERE message_id=? AND "
                "recipient_id=?",
                (message_id, actor.agent_id),
            ).fetchone()
            if row is None or row["deliveries"] == 0:
                raise SwarmAccessError("Only a delivered addressed message can be acknowledged")
            if row["acknowledged_at"] is None:
                connection.execute(
                    "UPDATE deliveries SET acknowledged_at=? WHERE message_id=? AND recipient_id=?",
                    (store.clock(), message_id, actor.agent_id),
                )
            return True

    def subscribe(self, actor: SwarmActor, topic: str, task_id: str = "") -> None:
        from jarvis.swarm.store import SwarmConflictError

        if not topic or len(topic) > 100:
            raise ValueError("A bounded topic is required")
        store = cast("TeamStore", self)
        with store._tx(write=True) as connection:
            store._actor(connection, actor, writing=True)
            store._require_tool(connection, "send_message")
            if task_id:
                store._task(connection, task_id)
            if connection.execute(
                "SELECT 1 FROM subscriptions WHERE agent_id=? AND task_id=? AND topic=?",
                (actor.agent_id, task_id, topic),
            ).fetchone():
                return
            count = connection.execute(
                "SELECT count(*) FROM subscriptions WHERE agent_id=?", (actor.agent_id,)
            ).fetchone()[0]
            if count >= 32:
                raise SwarmConflictError("At most 32 subscriptions per logical member")
            connection.execute(
                "INSERT OR IGNORE INTO subscriptions VALUES (?,?,?)",
                (actor.agent_id, task_id, topic),
            )

    def unsubscribe(self, actor: SwarmActor, topic: str, task_id: str = "") -> None:
        store = cast("TeamStore", self)
        with store._tx(write=True) as connection:
            store._actor(connection, actor)
            connection.execute(
                "DELETE FROM subscriptions WHERE agent_id=? AND task_id=? AND topic=?",
                (actor.agent_id, task_id, topic),
            )

    def discover(self, actor: SwarmActor, task_id: str, *, limit: int = 16) -> list[dict[str, Any]]:
        from jarvis.swarm.store import _page

        store = cast("TeamStore", self)
        _page(limit)
        with store._tx() as connection:
            store._actor(connection, actor)
            task = store._task(connection, task_id)
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT a.record FROM agents a WHERE a.active=1 AND a.id<>? AND "
                    "(a.role IN ('lead','coordinator') OR json_extract(a.record,'$.domain')=? "
                    "OR json_extract(a.record,'$.task_id') IN (SELECT depends_on FROM "
                    "dependencies WHERE task_id=?)) "
                    "ORDER BY a.role='coordinator' DESC,a.role='lead' DESC,a.id LIMIT ?",
                    (actor.agent_id, task["domain"], task_id, limit),
                )
            ]

    def search(
        self,
        actor: SwarmActor,
        query: str,
        *,
        kind: str = "tasks",
        task_id: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        from jarvis.swarm.store import _page

        if not query or len(query) > 200:
            raise ValueError("Use a search query from 1 to 200 characters")
        _page(limit, offset)
        tables = {
            "tasks": "tasks",
            "messages": "messages",
            "artifacts": "artifacts",
            "decisions": "decisions",
        }
        if kind not in tables:
            raise ValueError("Search tasks, messages, artifacts or decisions")
        store = cast("TeamStore", self)
        with store._tx() as connection:
            store._actor(connection, actor)
            store._require_tool(connection, "search_team")
            if task_id:
                store._task(connection, task_id)
            return [
                json.loads(row[0])
                for row in connection.execute(
                    f"SELECT record FROM {tables[kind]} WHERE instr(lower(record),lower(?))>0 "  # noqa: S608
                    "AND (?='' OR json_extract(record,'$.task_id')=? OR "
                    "json_extract(record,'$.id')=?) "
                    "ORDER BY rowid DESC LIMIT ? OFFSET ?",
                    (query, task_id, task_id, task_id, limit, offset),
                )
            ]
