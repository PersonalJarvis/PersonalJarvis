"""A bounded PostgreSQL transaction adapter for the shared store algorithms.

The adapter accepts trusted repository SQL only, never worker SQL. Values remain
driver parameters. Explicit PostgreSQL row locks replace SQLite's write lock;
Redis participates in neither locking, fencing nor authorization.
"""

from __future__ import annotations

import json
import re
import threading
import weakref
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, SwarmStoreError

from .config import DistributedConfig, DistributedSecrets

_NUMERIC_JSON_FIELDS = {"priority", "created_at", "reliability", "level"}
_JSON_EXTRACT = re.compile(r"json_extract\(([a-z_.]+),'\$\.([a-z_]+)'\)")
_TEAM_ID = re.compile(r"^[0-9a-f]{32}$")
_CONTROL_LANE: ContextVar[bool] = ContextVar("swarm_distributed_control_lane", default=False)


@contextmanager
def control_lane(enabled: bool = True):
    """Reserve bounded DB capacity for user controls, recovery and projections."""
    token = _CONTROL_LANE.set(enabled)
    try:
        yield
    finally:
        _CONTROL_LANE.reset(token)


class _WriteAdmission:
    """Process-local prioritization only; PostgreSQL still supplies correctness."""

    def __init__(self):
        self.lock = threading.Lock()
        self.busy = False
        self.controls = deque()
        self.workers = deque()

    @contextmanager
    def enter(self):
        waiter = threading.Event()
        queue = self.controls if _CONTROL_LANE.get() else self.workers
        with self.lock:
            if self.busy:
                queue.append(waiter)
            else:
                self.busy = True
                waiter.set()
        if not waiter.wait(300):
            with self.lock:
                if not waiter.is_set():
                    queue.remove(waiter)
                    raise SwarmConflictError("Distributed write queue timed out")
        try:
            yield
        finally:
            with self.lock:
                next_queue = self.controls or self.workers
                if next_queue:
                    next_queue.popleft().set()
                else:
                    self.busy = False


def team_schema(namespace: str, team_id: str) -> str:
    if not _TEAM_ID.fullmatch(team_id):
        raise SwarmAccessError("Invalid distributed team selector")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,19}", namespace):
        raise SwarmAccessError("Invalid distributed namespace")
    return namespace + "_t_" + team_id


def translate(statement: str) -> str:
    """Translate the small documented SQL dialect used by shared store methods."""

    def extract(match: re.Match[str]) -> str:
        column, key = match.groups()
        result = f"({column}->>'{key}')"
        if key in {"priority", "level"}:
            return result + "::bigint"
        return result + "::double precision" if key in _NUMERIC_JSON_FIELDS else result

    statement = _JSON_EXTRACT.sub(extract, statement)
    statement = re.sub(r"\browid\b", "__swarm_order", statement)
    statement = statement.replace(
        "instr(lower(record),lower(?))", "strpos(lower(record::text),lower(?))"
    )
    statement = statement.replace("(? OR d.acknowledged_at", "(?<>0 OR d.acknowledged_at")
    if statement.startswith("INSERT OR IGNORE "):
        statement = statement.replace("INSERT OR IGNORE ", "INSERT ", 1) + " ON CONFLICT DO NOTHING"
    if re.search(r"\b(PRAGMA|ATTACH|DETACH)\b", statement, re.IGNORECASE):
        raise SwarmStoreError("SQLite-only operation is unavailable in distributed storage")
    # Parameter markers in string literals (including escaped apostrophes) stay
    # literal, and literal percent signs must be escaped for psycopg's binding.
    pieces = re.split(r"('(?:''|[^'])*')", statement)
    return "".join(
        piece.replace("%", "%%") if index % 2 else piece.replace("%", "%%").replace("?", "%s")
        for index, piece in enumerate(pieces)
    )


class Row(Mapping[str, Any]):
    """Mapping/index row compatible with the shared domain methods."""

    def __init__(self, names: Sequence[str], values: Sequence[Any]):
        self._names = tuple(names)
        self._values = tuple(
            json.dumps(value, separators=(",", ":"), ensure_ascii=False)
            if isinstance(value, (dict, list))
            else value
            for value in values
        )

    def __getitem__(self, key):
        return self._values[key if isinstance(key, int) else self._names.index(key)]

    def __iter__(self):
        return iter(self._names)

    def __len__(self):
        return len(self._names)


class Cursor:
    def __init__(self, cursor: Any, *, inserted_event: bool = False):
        self.raw = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = cursor.fetchone()[0] if inserted_event else None

    def fetchone(self):
        row = self.raw.fetchone()
        return (
            Row([column.name for column in self.raw.description], row) if row is not None else None
        )

    def fetchall(self):
        names = [column.name for column in self.raw.description]
        return [Row(names, row) for row in self.raw.fetchall()]

    def __iter__(self) -> Iterator[Row]:
        names = [column.name for column in self.raw.description]
        for row in self.raw:
            yield Row(names, row)


class Connection:
    """Database protocol facade; every method runs within an owning transaction."""

    def __init__(self, connection: Any):
        self.raw = connection

    def execute(self, statement: str, parameters: Sequence[Any] = ()) -> Cursor:
        statement = translate(statement)
        inserted_event = statement.startswith("INSERT INTO events ")
        if inserted_event:
            statement += " RETURNING seq"
        return Cursor(self.raw.execute(statement, parameters), inserted_event=inserted_event)

    def executemany(self, statement: str, parameters: Iterable[Sequence[Any]]) -> Cursor:
        cursor = self.raw.cursor()
        cursor.executemany(translate(statement), parameters)
        return Cursor(cursor)


class PostgresDatabase:
    def __init__(self, config: DistributedConfig, secrets: DistributedSecrets, *, pool=None):
        self.config, self.secrets = config, secrets
        self._pool = pool
        self._control_pool = pool
        self._initialization = threading.Lock()
        self._admission_lock = threading.Lock()
        self._admissions: weakref.WeakValueDictionary[str, _WriteAdmission] = (
            weakref.WeakValueDictionary()
        )

    @staticmethod
    def validate_role(connection) -> None:
        row = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE "
            "(rolsuper OR rolbypassrls) AND pg_has_role(oid,'MEMBER'))"
        ).fetchone()
        if row is None or row[0]:
            raise SwarmAccessError(
                "Use a dedicated PostgreSQL role without superuser/BYPASSRLS membership"
            )

    def pool(self):
        attribute = "_control_pool" if _CONTROL_LANE.get() else "_pool"
        current = getattr(self, attribute)
        if current is not None:
            return current
        with self._initialization:
            current = getattr(self, attribute)
            if current is not None:
                return current
            self.secrets.validate()
            try:
                from psycopg_pool import ConnectionPool
            except ImportError:
                raise SwarmStoreError(
                    "Install the optional swarm-distributed dependencies in the application"
                ) from None
            parsed = urlsplit(self.config.postgres_dsn)
            kwargs = dict(
                host=parsed.hostname,
                port=parsed.port or 5432,
                dbname=unquote(parsed.path.lstrip("/")),
                user=unquote(parsed.username or ""),
                password=self.secrets.postgres_password,
                sslmode=parse_qs(parsed.query).get("sslmode", ["verify-full"])[0],
                connect_timeout=5,
                application_name="personaljarvis-swarm",
            )
            if kwargs["sslmode"] == "verify-full":
                # libpq otherwise expects a manually installed root.crt.
                # Use the maintained bundled public CA set explicitly.
                import certifi

                kwargs["sslrootcert"] = certifi.where()

            def configure(connection):
                self.validate_role(connection)
                connection.commit()

            reserved = min(2, self.config.max_connections - 1)
            current = ConnectionPool(
                "",
                kwargs=kwargs,
                min_size=0,
                max_size=(
                    reserved if _CONTROL_LANE.get() else self.config.max_connections - reserved
                ),
                max_waiting=2048,
                timeout=10,
                reconnect_timeout=10,
                configure=configure,
                open=True,
            )
            setattr(self, attribute, current)
            return current

    @contextmanager
    def transaction(self, *, team_id: str = "", write: bool = False, owner: str | None = None):
        if team_id and write:
            # Keep bulk writers outside the pool until one can use the team row.
            # Otherwise 30 waiters occupy PostgreSQL row-lock queues ahead of
            # each heartbeat even when control connections are reserved.
            with self._admission_lock:
                admission = self._admissions.get(team_id)
                if admission is None:
                    admission = _WriteAdmission()
                    self._admissions[team_id] = admission
            with (
                admission.enter(),
                self._transaction(team_id=team_id, write=write, owner=owner) as connection,
            ):
                yield connection
        else:
            with self._transaction(team_id=team_id, write=write, owner=owner) as connection:
                yield connection

    @contextmanager
    def _transaction(self, *, team_id: str = "", write: bool = False, owner: str | None = None):
        pool = self.pool()
        try:
            with pool.connection() as raw, raw.transaction():
                if not write:
                    # Match SQLite WAL readers: one consistent MVCC snapshot
                    # without excluding heartbeats, stop controls or other writers.
                    raw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                raw.execute("SET LOCAL statement_timeout='15000ms'")
                raw.execute("SET LOCAL lock_timeout='10000ms'")
                raw.execute("SET LOCAL idle_in_transaction_session_timeout='30000ms'")
                namespace = self.config.namespace
                if team_id:
                    schema = team_schema(namespace, team_id)
                    # Each operation rechecks a still-present catalog owner entry.
                    query = f'SELECT id FROM "{namespace}".teams WHERE id=%s'  # noqa: S608
                    parameters = [team_id]
                    if owner is not None:
                        query += " AND owner=%s"
                        parameters.append(owner)
                    if write:
                        query += " FOR UPDATE"
                    if raw.execute(query, parameters).fetchone() is None:
                        raise SwarmAccessError("Team unavailable for this owner")
                    raw.execute("SELECT set_config('swarm.team_id',%s,true)", (team_id,))
                else:
                    schema = namespace
                raw.execute("SELECT set_config('search_path',%s,true)", (f'"{schema}",pg_catalog',))
                yield Connection(raw)
        except (SwarmAccessError, SwarmStoreError, ValueError):
            raise
        except Exception as error:
            # Constraint failures are domain conflicts; do not expose SQL content,
            # hostnames or connection credentials in user-facing error strings.
            code = getattr(error, "sqlstate", "") or ""
            if code.startswith("23") or code in {"40001", "40P01", "55P03"}:
                raise SwarmConflictError(
                    "Distributed transaction conflicted; reload and retry"
                ) from None
            raise SwarmStoreError(
                "Distributed database unavailable; verify its connection in settings"
            ) from None

    def close(self):
        if self._control_pool is not None and self._control_pool is not self._pool:
            self._control_pool.close()
        self._control_pool = None
        if self._pool is not None:
            self._pool.close()
            self._pool = None
