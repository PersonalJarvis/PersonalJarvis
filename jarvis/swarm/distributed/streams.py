"""At-least-once Redis delivery with PostgreSQL outbox recovery.

Streams carry bounded references, never worker text, tokens or authority.
Consumers reread authorized PostgreSQL state. A Redis flush, restart or trimmed
pending entry loses no committed work: unacknowledged outbox rows are republished.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

from jarvis.swarm.store import SwarmAccessError, SwarmStoreError, _page

from .database import team_schema


@dataclass(frozen=True)
class DeliveryHint:
    team_id: str
    event_id: str
    event_seq: str
    stream_id: str


class RedisDelivery:
    """One bounded client pool for every team, independent of worker count."""

    def __init__(self, config, secrets, *, client=None):
        self.config, self.secrets = config, secrets
        self._client = client
        self._lock = threading.Lock()
        self._claims: dict[str, str] = {}
        from .coordination_cache import RedisCoordinationCache

        self.coordination = RedisCoordinationCache(self)

    def client(self):
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                self.secrets.validate()
                try:
                    import redis
                except ImportError:
                    raise SwarmStoreError(
                        "Install optional swarm-distributed dependencies in the application"
                    ) from None
                pool = redis.BlockingConnectionPool.from_url(
                    self.config.redis_url,
                    password=self.secrets.redis_password,
                    max_connections=self.config.max_connections,
                    timeout=5,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                    decode_responses=True,
                )
                self._client = redis.Redis(connection_pool=pool)
            return self._client

    def stream(self, team_id: str):
        team_schema(self.config.namespace, team_id)
        return f"{self.config.namespace}:{{{team_id}}}:events"

    def check_connection(self):
        try:
            self.client().ping()
        except SwarmStoreError:
            raise
        except Exception:
            raise SwarmStoreError(
                "Redis unavailable; verify its endpoint and credentials in settings"
            ) from None

    def pump(self, store, *, limit=100):
        _page(limit)
        # Delivery I/O never holds a PostgreSQL lock. Competing pumps may publish
        # duplicates, which are safe because every durable event/effect is keyed.
        with store._tx() as connection:
            rows = connection.execute(
                "SELECT event_id,event_seq FROM delivery_outbox WHERE acknowledged_at IS NULL "
                "AND (published_at IS NULL OR published_at<=?) ORDER BY event_seq LIMIT ?",
                (store.clock() - 30, limit),
            ).fetchall()
        for row in rows:
            try:
                self.client().xadd(
                    self.stream(store.team_id),
                    {"event_id": row["event_id"], "event_seq": str(row["event_seq"])},
                    maxlen=10_000,
                    approximate=False,
                )
                self.client().expire(self.stream(store.team_id), 7 * 86400)
            except Exception:
                raise SwarmStoreError(
                    "Redis delivery unavailable; committed events remain queued in PostgreSQL"
                ) from None
        if rows:
            with store._tx(write=True) as connection:
                connection.executemany(
                    "UPDATE delivery_outbox SET published_at=?,attempts=attempts+1 "
                    "WHERE event_id=? AND acknowledged_at IS NULL",
                    [(store.clock(), row["event_id"]) for row in rows],
                )
        return len(rows)

    def poll(self, store, consumer: str, *, limit=100) -> list[DeliveryHint]:
        _page(limit)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", consumer):
            raise ValueError("Use a bounded consumer identity")
        stream = self.stream(store.team_id)
        client = self.client()
        try:
            try:
                client.xgroup_create(stream, "controller", id="0-0", mkstream=True)
            except Exception as error:
                if "BUSYGROUP" not in str(error):
                    raise
            client.expire(stream, 7 * 86400)
            reclaimed = client.xautoclaim(
                stream,
                "controller",
                consumer,
                30_000,
                self._claims.get(store.team_id, "0-0"),
                count=limit,
            )
            self._claims[store.team_id] = reclaimed[0]
            # Bound process-local replay cursors independently of historical teams.
            if len(self._claims) > 256:
                self._claims.pop(next(iter(self._claims)))
            items = list(reclaimed[1])
            if len(items) < limit:
                batches = client.xreadgroup(
                    "controller", consumer, {stream: ">"}, count=limit - len(items)
                )
                items.extend(item for _, entries in batches for item in entries)
        except Exception:
            raise SwarmStoreError(
                "Redis delivery read failed; PostgreSQL state remains authoritative"
            ) from None
        result = []
        stale = []
        with store._tx() as connection:
            for stream_id, fields in items:
                event_id = fields.get("event_id", "")
                sequence = fields.get("event_seq", "")
                # Even a forged broker reference must resolve inside this team's
                # RLS namespace before the trusted consumer sees an event hint.
                if not re.fullmatch(r"[a-f0-9]{32}", event_id) or not re.fullmatch(
                    r"[0-9]{1,19}", sequence
                ):
                    stale.append(stream_id)
                    continue
                row = connection.execute(
                    "SELECT event_seq,acknowledged_at FROM delivery_outbox WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if (
                    row is None
                    or str(row["event_seq"]) != sequence
                    or row["acknowledged_at"] is not None
                ):
                    stale.append(stream_id)
                    continue
                result.append(DeliveryHint(store.team_id, event_id, sequence, stream_id))
        if stale:
            try:
                client.xack(stream, "controller", *stale)
            except Exception:
                raise SwarmStoreError("Redis stale-delivery acknowledgement failed") from None
        return result

    def ack(self, store, hint: DeliveryHint):
        """Call only after the trusted consumer's idempotent effect has committed."""
        return self.ack_many(store, [hint])

    def ack_many(self, store, hints: list[DeliveryHint]):
        """Commit a bounded consumer batch before a single broker acknowledgement."""
        if not hints:
            return
        _page(len(hints))
        if any(hint.team_id != store.team_id for hint in hints):
            raise SwarmAccessError("Delivery acknowledgement belongs to another team")
        with store._tx(write=True) as connection:
            for hint in hints:
                row = connection.execute(
                    "SELECT event_seq FROM delivery_outbox WHERE event_id=?", (hint.event_id,)
                ).fetchone()
                if row is None or str(row[0]) != hint.event_seq:
                    raise SwarmAccessError("Delivery acknowledgement has no committed event")
            connection.executemany(
                "UPDATE delivery_outbox SET acknowledged_at=? WHERE event_id=?",
                [(store.clock(), hint.event_id) for hint in hints],
            )
        try:
            self.client().xack(
                self.stream(store.team_id), "controller", *[hint.stream_id for hint in hints]
            )
        except Exception:
            # PG already acknowledged it; a retry/redelivery is filtered by poll.
            raise SwarmStoreError(
                "Redis acknowledgement failed; durable acknowledgement was retained"
            ) from None

    def delete_team(self, team_id: str):
        """After catalog deletion, remove this team's stream and consumer group."""
        try:
            self.client().delete(self.stream(team_id))
        except Exception:
            raise SwarmStoreError(
                "Redis cleanup pending; its team stream will expire automatically"
            ) from None
        self._claims.pop(team_id, None)
        self.coordination.discard(team_id)

    def close(self):
        if self._client is not None:
            self._client.close()
            pool: Any = getattr(self._client, "connection_pool", None)
            if pool is not None:
                pool.disconnect()
            self._client = None
