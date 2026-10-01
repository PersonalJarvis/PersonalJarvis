"""Transactional team namespaces with the same controller and worker contracts."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jarvis.core.swarm_types import TaskSpec, TeamCreate, TeamRecord, TeamState
from jarvis.swarm.store import (
    SwarmAccessError,
    SwarmConflictError,
    SwarmStoreError,
    TeamStore,
    _id,
    _json,
    _page,
    _validate_graph,
    creation_fingerprint,
)

from .artifacts import RemoteArtifacts
from .config import DistributedConfig, DistributedSecrets
from .database import PostgresDatabase, control_lane, team_schema
from .lifetime import RegistryLifetime, registry_operation

SCHEMA_VERSION = 1
log = logging.getLogger(__name__)
TABLES = (
    "team",
    "controller",
    "agents",
    "tasks",
    "dependencies",
    "attempts",
    "reservations",
    "messages",
    "deliveries",
    "subscriptions",
    "artifacts",
    "verifications",
    "ratings",
    "reputation",
    "team_tools",
    "destinations",
    "publications",
    "decisions",
    "counters",
    "events",
    "delivery_outbox",
    "object_uploads",
)
_SCOPED_RECORDS = {
    "agents",
    "tasks",
    "messages",
    "artifacts",
    "verifications",
    "ratings",
    "team_tools",
    "destinations",
    "publications",
    "decisions",
    "events",
}


class PostgresTeamRegistry:
    """Trusted server catalog; neither registry nor DB pool enters a worker set."""

    def __init__(
        self,
        config: DistributedConfig,
        secrets: DistributedSecrets,
        *,
        clock: Callable[[], float] = time.time,
        database=None,
        objects=None,
        delivery=None,
    ):
        self.config = DistributedConfig.model_validate(config)
        self.clock = clock
        self._provisioned = False
        self.lifetime = RegistryLifetime()
        self._cleanup_lock = threading.Lock()
        self._close_pending: list[Any] = []
        self.lifetime.on_unused(self.close_if_unused)
        self.database = database or PostgresDatabase(self.config, secrets)
        from .objects import S3Objects
        from .streams import RedisDelivery

        self.objects = objects or S3Objects(self.config, secrets)
        self.delivery = delivery or RedisDelivery(self.config, secrets)

    @registry_operation
    def provision(self) -> None:
        """Create only the bounded catalog, after rejecting unsafe PG privileges."""
        if self._provisioned:
            return
        namespace = self.config.namespace
        with self.database.transaction(write=True) as connection:
            raw = connection.raw
            self.database.validate_role(raw)
            # PostgreSQL advisory locking is used only for first-time namespace
            # DDL. Every normal team operation uses durable catalog row locks.
            raw.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (namespace,))
            raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{namespace}"')  # noqa: S608
            ownership = raw.execute(
                "SELECT nspowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) "
                "FROM pg_namespace WHERE nspname=%s",
                (namespace,),
            ).fetchone()
            if ownership is None or not ownership[0]:
                raise SwarmAccessError(
                    "Distributed namespace must belong to its dedicated application role"
                )
            raw.execute(f'REVOKE ALL ON SCHEMA "{namespace}" FROM PUBLIC')  # noqa: S608
            raw.execute(
                "CREATE TABLE IF NOT EXISTS registry_lock (singleton BIGINT "
                "PRIMARY KEY CHECK(singleton=1))"
            )
            raw.execute("INSERT INTO registry_lock VALUES (1) ON CONFLICT DO NOTHING")
            raw.execute(
                "CREATE TABLE IF NOT EXISTS teams (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                "request_key TEXT NOT NULL, name TEXT NOT NULL, created_at DOUBLE "
                "PRECISION NOT NULL, "
                "spec_hash TEXT NOT NULL, schema_version INTEGER NOT NULL, "
                "UNIQUE(owner,request_key))"
            )
            raw.execute("CREATE INDEX IF NOT EXISTS teams_owner ON teams(owner,created_at DESC,id)")
            raw.execute(
                "CREATE TABLE IF NOT EXISTS team_deletions (team_id TEXT NOT NULL, "
                "request_key TEXT NOT NULL, owner TEXT NOT NULL, deleted_at DOUBLE PRECISION "
                "NOT NULL, result JSONB NOT NULL, PRIMARY KEY(team_id,request_key))"
            )
            raw.execute("REVOKE ALL ON ALL TABLES IN SCHEMA " + namespace + " FROM PUBLIC")
        self._provisioned = True

    @registry_operation
    def check_connection(self) -> dict[str, bool]:
        self.provision()
        self.delivery.check_connection()
        self.objects.check_connection()
        return {"postgresql": True, "redis": True, "s3": True}

    def _store(self, team_id: str, owner: str) -> PostgresTeamStore:
        return PostgresTeamStore(self, team_id, owner)

    @registry_operation
    def create(self, spec: TeamCreate, owner: str = "local-user") -> dict[str, Any]:
        spec = TeamCreate.model_validate(spec)
        if spec.mode != "distributed":
            raise ValueError("The PostgreSQL registry only accepts explicitly distributed teams")
        if not owner or len(owner) > 200:
            raise ValueError("A bounded owner identity is required")
        _validate_graph(spec.tasks, {})
        self.provision()
        fingerprint = creation_fingerprint(spec)
        with self.database.transaction(write=True) as connection:
            connection.execute("SELECT singleton FROM registry_lock WHERE singleton=1 FOR UPDATE")
            existing = connection.execute(
                "SELECT id,spec_hash FROM teams WHERE owner=? AND request_key=?",
                (owner, spec.request_key),
            ).fetchone()
            if existing:
                if existing["spec_hash"] != fingerprint:
                    raise SwarmConflictError("Creation key already identifies a different team")
                team_id = existing["id"]
            else:
                team_id = _id()
                connection.execute(
                    "INSERT INTO teams VALUES (?,?,?,?,?,?,?)",
                    (
                        team_id,
                        owner,
                        spec.request_key,
                        spec.name,
                        self.clock(),
                        fingerprint,
                        SCHEMA_VERSION,
                    ),
                )
                # Catalog registration, schema, task graph and initial lead commit
                # atomically; a lost response can safely retry the same request key.
                self._store(team_id, owner)._initialize_in(connection, spec)
        return self.open(team_id, owner).get()

    @registry_operation
    def list(self, owner: str = "local-user", *, limit: int = 50, offset: int = 0):
        limit, offset = _page(limit, offset)
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT id,name,created_at FROM teams WHERE owner=? "
                "ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                (owner, limit, offset),
            ).fetchall()
        records = []
        for row in rows:
            try:
                records.append(self.open(row["id"], owner).get())
            except Exception:  # noqa: BLE001 - keep corrupt namespaces from blocking healthy teams
                log.exception("Distributed team %s requires recovery", row["id"])
                records.append(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "created_at": row["created_at"],
                        "available": False,
                        "error": "Team storage is unavailable; repair storage and retry recovery.",
                    }
                )
        return records

    @registry_operation
    def open(self, team_id: str, owner: str = "local-user") -> PostgresTeamStore:
        team_schema(self.config.namespace, team_id)
        store = self._store(team_id, owner)
        store.check_schema()
        return store

    @registry_operation
    def deletion_result(self, team_id: str, owner: str, request_key: str):
        team_schema(self.config.namespace, team_id)
        self.provision()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT result FROM team_deletions WHERE team_id=? AND owner=? AND request_key=?",
                (team_id, owner, request_key),
            ).fetchone()
        return json.loads(row[0]) if row else None

    @registry_operation
    def delete(
        self,
        team_id: str,
        owner: str = "local-user",
        *,
        request_key: str | None = None,
        expected_storage_generation: str | None = None,
    ):
        schema = team_schema(self.config.namespace, team_id)
        request_key = request_key or "legacy-" + team_id
        if not 1 <= len(request_key) <= 100:
            raise ValueError("A bounded deletion operation key is required")
        self.provision()
        result = {"team_id": team_id, "deleted": True, "objects": "retained_for_bucket_lifecycle"}
        with self.database.transaction(write=True) as connection:
            connection.execute("SELECT singleton FROM registry_lock WHERE singleton=1 FOR UPDATE")
            deleted = connection.execute(
                "SELECT owner,result FROM team_deletions WHERE team_id=? AND request_key=?",
                (team_id, request_key),
            ).fetchone()
            if deleted:
                if deleted["owner"] != owner:
                    raise SwarmAccessError("Deletion belongs to a different owner")
                return json.loads(deleted["result"])
            registered = connection.execute(
                "SELECT owner FROM teams WHERE id=? FOR UPDATE", (team_id,)
            ).fetchone()
            if registered is None or registered["owner"] != owner:
                raise SwarmAccessError("Team unavailable for this owner")
            connection.raw.execute(
                "SELECT set_config('search_path',%s,true)", (f'"{schema}",pg_catalog',)
            )
            connection.raw.execute("SELECT set_config('swarm.team_id',%s,true)", (team_id,))
            store = self._store(team_id, owner)
            if (
                expected_storage_generation is not None
                and store._team(connection).get("storage_generation", "")
                != expected_storage_generation
            ):
                raise SwarmConflictError("The team was restored; refresh before deleting it")
            if store._team(connection)["state"] not in {
                "succeeded",
                "failed",
                "canceled",
                "archived",
            }:
                raise SwarmConflictError(
                    "Archive or terminate the team before deleting its workspace"
                )
            if connection.execute(
                "SELECT 1 FROM publications WHERE state='pending' LIMIT 1"
            ).fetchone():
                raise SwarmConflictError(
                    "Finish or revoke pending publications before deleting this team"
                )
            connection.raw.execute(f'DROP SCHEMA "{schema}" CASCADE')  # noqa: S608
            connection.raw.execute(
                "SELECT set_config('search_path',%s,true)",
                (f'"{self.config.namespace}",pg_catalog',),
            )
            connection.raw.execute(
                f'DELETE FROM "{self.config.namespace}".teams WHERE id=%s AND owner=%s',  # noqa: S608
                (team_id, owner),
            )
            connection.execute(
                "INSERT INTO team_deletions VALUES (?,?,?,?,?)",
                (team_id, request_key, owner, self.clock(), _json(result)),
            )
        # Publication adapters copy selected bytes before marking publication
        # complete. Unreferenced team objects are intentionally left for an S3
        # lifecycle rule, never deleted before PostgreSQL commits the removal.
        cleanup_pending = False
        try:
            self.delivery.delete_team(team_id)
        except SwarmStoreError:
            cleanup_pending = True
            log.warning("Deleted Swarm team stream awaits bounded Redis expiration")
        return dict(result, delivery_cleanup_pending=cleanup_pending)

    @registry_operation
    def restore(self, source, *, owner="local-user", request_key, replace_existing=False):
        from .snapshot import restore

        return restore(
            self, source, owner=owner, request_key=request_key, replace_existing=replace_existing
        )

    def retire(self):
        self.lifetime.retire()

    def close_if_unused(self) -> bool:
        with self._cleanup_lock:
            if self.lifetime.claim_close():
                self._close_pending = [self.database, self.objects, self.delivery]
            for resource in list(self._close_pending):
                try:
                    resource.close()
                except Exception:  # noqa: BLE001 - close every independently owned resource
                    log.exception("Retired distributed resource cleanup failed")
                else:
                    self._close_pending.remove(resource)
            return self.lifetime.closed and not self._close_pending

    def close(self):
        self.retire()
        return self.close_if_unused()


class PostgresTeamStore(RemoteArtifacts, TeamStore):
    """All domain transitions are inherited; only storage primitives differ."""

    def __init__(self, registry: PostgresTeamRegistry, team_id: str, owner: str):
        self.registry = registry
        self.team_id = team_id
        self.owner = owner
        self.clock = registry.clock
        registry.lifetime.track(self)

    def __copy__(self):
        # Transaction-bound artifact facades also retain the registry across retirement.
        copied = object.__new__(type(self))
        copied.__dict__.update(self.__dict__)
        self.registry.lifetime.track(copied, inherited=True)
        return copied

    def _tx(self, *, write: bool = False, provision: bool = False):
        return self.registry.database.transaction(
            team_id=self.team_id, owner=self.owner, write=write
        )

    def check_schema(self):
        with self._tx() as connection:
            row = connection.raw.execute(
                f'SELECT schema_version FROM "{self.registry.config.namespace}".teams WHERE id=%s',  # noqa: S608
                (self.team_id,),
            ).fetchone()
            if row is None or row[0] != SCHEMA_VERSION:
                raise SwarmStoreError(
                    "Distributed team schema requires a compatible application version"
                )
            self._team(connection)

    def discover(self, actor, task_id: str, *, limit: int = 16):
        peers = super().discover(actor, task_id, limit=limit)
        return self.registry.delivery.coordination.enrich(self, actor, peers)

    def initialize(self, spec: TeamCreate):
        # Only registry.create owns provisioning to avoid unregistered namespaces.
        raise SwarmAccessError("Create distributed teams through the authorized registry")

    def _create_schema(self, connection):
        raw = connection.raw
        schema = team_schema(self.registry.config.namespace, self.team_id)
        raw.execute(f'CREATE SCHEMA "{schema}"')  # noqa: S608
        raw.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC')  # noqa: S608
        raw.execute("SELECT set_config('search_path',%s,true)", (f'"{schema}",pg_catalog',))
        raw.execute("SELECT set_config('swarm.team_id',%s,true)", (self.team_id,))
        for statement in (
            Path(__file__).with_name("schema.sql").read_text(encoding="utf-8").split(";")
        ):
            if statement.strip():
                raw.execute(statement)
        raw.execute(
            "CREATE TABLE delivery_outbox (event_id TEXT PRIMARY KEY REFERENCES events(id), "
            "event_seq BIGINT NOT NULL, published_at DOUBLE PRECISION, "
            "attempts BIGINT NOT NULL DEFAULT 0, "
            "acknowledged_at DOUBLE PRECISION)"
        )
        raw.execute(
            "CREATE INDEX outbox_pending ON delivery_outbox(event_seq) "
            "WHERE acknowledged_at IS NULL"
        )
        raw.execute(
            "CREATE TABLE object_uploads (id TEXT PRIMARY KEY, "
            "owner_id TEXT NOT NULL REFERENCES agents(id), "
            "task_id TEXT NOT NULL REFERENCES tasks(id), fingerprint TEXT NOT NULL, "
            "size_bytes BIGINT NOT NULL "
            "CHECK(size_bytes BETWEEN 0 AND 10000000), state TEXT NOT NULL "
            "CHECK(state IN ('reserved','unknown','completed')), "
            "created_at DOUBLE PRECISION NOT NULL, task_fence BIGINT NOT NULL, "
            "sha256 TEXT NOT NULL, "
            "name TEXT NOT NULL, media_type TEXT NOT NULL, request_key TEXT NOT NULL, "
            "recovered_artifact_id TEXT NOT NULL DEFAULT '')"
        )
        raw.execute(
            "CREATE INDEX object_uploads_pending ON object_uploads(state) WHERE state<>'completed'"
        )
        for table in TABLES:
            # Identifiers are compile-time constants and a validated hex team ID.
            raw.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')  # noqa: S608
            raw.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')  # noqa: S608
            if table not in {"events", "delivery_outbox", "object_uploads"}:
                raw.execute(f'CREATE INDEX "{table}_insertion_order" ON "{table}"(__swarm_order)')  # noqa: S608
            guard = f"current_setting('swarm.team_id',true)='{self.team_id}'"
            check = guard
            if table in _SCOPED_RECORDS:
                check += " AND record->>'team_id'=current_setting('swarm.team_id',true)"
            elif table == "team":
                check += " AND record->>'id'=current_setting('swarm.team_id',true)"
            raw.execute(
                f'CREATE POLICY team_scope ON "{table}" USING ({guard}) WITH CHECK ({check})'
            )  # noqa: S608
        self._add_constraints(raw)

    def _initialize_in(self, connection, spec: TeamCreate):
        self._create_schema(connection)
        now, lead_id = self.clock(), _id()
        record = TeamRecord(
            id=self.team_id,
            name=spec.name,
            goal=spec.goal,
            lead_id=lead_id,
            state=TeamState.CREATED,
            version=1,
            created_at=now,
            updated_at=now,
            acceptance=spec.acceptance or spec.goal,
            limits=spec.limits,
            policy=spec.policy,
            mode="distributed",
        ).model_dump(mode="json")
        connection.execute("INSERT INTO team (singleton,record) VALUES (1,?)", (_json(record),))
        self._insert_agent(connection, lead_id, "Team lead", "lead", "general", "coordination")
        self._insert_tasks(connection, spec.tasks)
        if spec.preparation_required:
            from ..preparation import initialize

            initialize(self, connection, spec)
        self._event(connection, "team.created", "Team created")

    @staticmethod
    def _add_constraints(raw):
        constraints = {
            "team": (
                "record->>'state' IN ('created','running','paused','blocked','succ"
                "eeded','failed','canceled','archived') AND "
                "jsonb_typeof(record->'state')='string'"
            ),
            "agents": (
                "state IN "
                "('idle','running','waiting','stopped','failed','completed') AND "
                "active IN (0,1) AND state IS NOT DISTINCT FROM record->>'state' "
                "AND role IS NOT DISTINCT FROM record->>'role' AND id IS NOT "
                "DISTINCT FROM record->>'id'"
            ),
            "tasks": (
                "state IS NOT DISTINCT FROM record->>'state' AND id IS NOT "
                "DISTINCT FROM record->>'id' AND fence IS NOT DISTINCT FROM "
                "(record->>'fence')::bigint AND owner_id IS NOT DISTINCT FROM "
                "record->>'owner_id'"
            ),
            "attempts": (
                "state IN "
                "('running','succeeded','failed','canceled','interrupted') AND "
                "fence>=1 AND controller_fence>=1"
            ),
            "reservations": (
                "state IN ('reserved','unknown','closed') AND tokens ~ "
                "'^[0-9]{1,31}$' AND cost_microusd ~ '^[0-9]{1,31}$'"
            ),
            "messages": (
                "intent IN ('COORD_STATUS','REQUEST_PROGRESS','REPORT_PROGRESS','R"
                "EQUEST_HELP','OFFER_HELP','SHARE_FINDING','CLAIM_WORK','RELEASE_W"
                "ORK','CONFLICT') AND intent IS NOT DISTINCT FROM "
                "record->>'intent' AND sender_id IS NOT DISTINCT FROM "
                "record->>'sender_id' AND task_id IS NOT DISTINCT FROM "
                "record->>'task_id'"
            ),
            "publications": (
                "state IN ('pending','published','revoked') AND state IS NOT "
                "DISTINCT FROM record->>'state'"
            ),
            "team_tools": (
                "state IN ('validated','revoked') AND state IS NOT DISTINCT FROM "
                "record->>'state' AND name IS NOT DISTINCT FROM record->>'name'"
            ),
        }
        for table, condition in constraints.items():
            raw.execute(
                f'ALTER TABLE "{table}" ADD CONSTRAINT storage_contract CHECK ({condition})'
            )  # noqa: S608

    def _put_object(self, data: bytes, digest: str):
        return self.registry.objects.put(self.team_id, digest, data)

    def add_tasks(self, controller, specs):
        specs = [TaskSpec.model_validate(spec) for spec in specs]
        if not 1 <= len(specs) <= 1000:
            raise ValueError("Add between 1 and 1000 tasks per operation")
        selectors = {spec.id for spec in specs}
        selectors.update(dependency for spec in specs for dependency in spec.dependencies)
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            if self._team(connection)["state"] in {"succeeded", "failed", "canceled", "archived"}:
                raise SwarmConflictError("Terminal team cannot add tasks")
            existing = {
                row[0]: []
                for row in connection.raw.execute(
                    "SELECT id FROM tasks WHERE id=ANY(%s)", (sorted(selectors),)
                )
            }
            # An already valid DAG cannot depend on a not-yet-created ID. Treat
            # its referenced existing nodes as roots without loading their bodies.
            _validate_graph(specs, existing)
            records = self._insert_tasks(connection, specs)
            self._event(
                connection, "task.graph_extended", "Tasks added", data={"count": len(specs)}
            )
            return records

    def _read_object(self, record):
        return self.registry.objects.read(self.team_id, record)

    def _event(self, connection, kind, summary, **kwargs):
        event = super()._event(connection, kind, summary, **kwargs)
        connection.execute(
            "INSERT INTO delivery_outbox (event_id,event_seq) VALUES (?,?)",
            (event["id"], int(event["seq"])),
        )
        return event

    def pump_delivery(self, *, limit: int = 100):
        return self.registry.delivery.pump(self, limit=limit)

    def world(self, group: str = ""):
        with control_lane():
            return super().world(group)

    def retain(self, controller, before):
        result = super().retain(controller, before)
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            cursor = connection.execute(
                "DELETE FROM delivery_outbox WHERE event_id IN "
                "(SELECT event_id FROM delivery_outbox WHERE acknowledged_at IS NOT NULL "
                "AND acknowledged_at<? ORDER BY event_seq LIMIT 200)",
                (before,),
            )
            result["acknowledged_delivery_hints"] = str(cursor.rowcount)
        return result

    def backup(self, destination):
        from .snapshot import backup

        return backup(self, destination)
