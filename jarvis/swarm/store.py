"""Lazy, isolated SQLite authority for opted-in Swarm teams.

Every worker operation revalidates an opaque membership token, task fence,
controller lease and lifecycle inside the same transaction as its effects.
Database handles are short lived and globally bounded, never per worker.
Call synchronous methods through ``asyncio.to_thread`` from runtime code.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path, PurePath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from jarvis.core.swarm_types import (
    AgentRecord,
    AgentRole,
    SwarmActor,
    SwarmController,
    TaskRecord,
    TaskSpec,
    TaskState,
    TeamCreate,
    TeamRecord,
    TeamState,
    decimal_counter,
)
from jarvis.swarm.mailbox import MailboxMixin
from jarvis.swarm.objects import ObjectMixin
from jarvis.swarm.reputation import initial_profile, observe

if TYPE_CHECKING:
    from .dispatch import DispatchBatch

SCHEMA_VERSION = 2
_CONNECTION_SLOTS = threading.BoundedSemaphore(16)
_CONNECTION_LOCAL = threading.local()
_MAX_CONNECTION_DEPTH = 3
_CLEANUP_TIMEOUT = 10.0
_TERMINAL = {"succeeded", "failed", "canceled", "archived"}
_TRANSITIONS = {
    "created": {"running", "canceled", "archived", "blocked"},
    "running": {"paused", "blocked", "succeeded", "failed", "canceled"},
    "paused": {"running", "canceled", "archived", "blocked"},
    "blocked": {"running", "canceled", "failed", "archived"},
    "succeeded": {"archived"},
    "failed": {"archived"},
    "canceled": {"archived"},
    "archived": set(),
}


class SwarmStoreError(RuntimeError):
    """Actionable storage or lifecycle conflict."""


class SwarmAccessError(PermissionError):
    """Missing, revoked, stale or out-of-scope authority."""


class SwarmConflictError(SwarmStoreError):
    """An optimistic version, lease or operation key conflicted."""


class SwarmBudgetError(SwarmStoreError):
    """The authorized reservation would exceed a configured limit."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _actor_digest(actor: SwarmActor) -> str:
    scope = f"|{actor.task_id}:{actor.task_fence}" if actor.task_id else ""
    return _digest(actor.token + scope)


def _id() -> str:
    return uuid.uuid4().hex


def _same_resolved_path(path: PurePath, resolved: PurePath) -> bool:
    """Accept only Windows' equivalent verbatim-prefix spelling, never relocation.

    CPython can retain the prefix when a volatile WAL file disappears between
    its two final-path probes. This spelling difference does not imply a link.
    """
    if path == resolved:
        return True
    if not path.drive or not resolved.drive:
        return False

    def normalized(value: PurePath) -> PureWindowsPath:
        text = str(value)
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
        return PureWindowsPath(text)

    return normalized(path) == normalized(resolved)


def _check_expected(team: dict[str, Any], version: int | None, generation: str | None) -> None:
    if version is not None and team["version"] != version:
        raise SwarmConflictError("Team changed; refresh before retrying this action")
    if generation is not None and team.get("storage_generation", "") != generation:
        raise SwarmConflictError("Team storage was restored; refresh before retrying this action")


def _ancestor_ids(connection: Any, task_id: str) -> set[str]:
    """Return only the immutable DAG's ancestors; callers still verify acceptance."""
    return {
        row[0]
        for row in connection.execute(
            "WITH RECURSIVE ancestors(id) AS ("
            "SELECT depends_on FROM dependencies WHERE task_id=? UNION "
            "SELECT d.depends_on FROM dependencies d JOIN ancestors a ON d.task_id=a.id) "
            "SELECT id FROM ancestors",
            (task_id,),
        )
    }


def task_contract_hash(task: TaskSpec | dict[str, Any]) -> str:
    """Hash the original task contract, excluding mutable execution fields."""
    if not isinstance(task, TaskSpec):
        task = TaskSpec.model_validate(
            {key: task[key] for key in TaskSpec.model_fields if key in task}
        )
    return _digest(json.dumps(task.model_dump(mode="json"), sort_keys=True, separators=(",", ":")))


def creation_fingerprint(spec: TeamCreate) -> str:
    """Preserve pre-preparation creation keys when the new opt-in flag is absent."""
    value = spec.model_dump(mode="json")
    if not value.get("preparation_required"):
        value.pop("preparation_required", None)
    return _digest(json.dumps(value, sort_keys=True))


def _page(limit: int, offset: int = 0) -> tuple[int, int]:
    if (
        type(limit) is not int
        or type(offset) is not int
        or not 1 <= limit <= 200
        or not 0 <= offset <= 10_000_000
    ):
        raise ValueError("Use a limit from 1 to 200 and a nonnegative bounded offset")
    return limit, offset


def _apply_schema(connection: sqlite3.Connection) -> None:
    """Execute complete SQL statements without executescript's implicit commit."""
    pending = ""
    for line in Path(__file__).with_name("schema.sql").read_text(encoding="utf-8").splitlines():
        pending += line + "\n"
        if sqlite3.complete_statement(pending):
            connection.execute(pending)
            pending = ""
    if pending.strip():
        raise SwarmStoreError("Incomplete packaged Swarm schema")


@contextmanager
def _connection_permit() -> Iterator[None]:
    """Reserve one synchronous operation, including its bounded nested handles.

    At most 16 threads hold permits. Each can open at most three connections:
    a catalog/controller transaction and two snapshot handles during backup or
    restore. The physical bound is therefore 48 handles per process. A nested
    handle uses its thread's reservation instead of competing with blocked
    outer writers, which otherwise can exhaust all permits before a commit.
    """
    depth = getattr(_CONNECTION_LOCAL, "depth", 0)
    if depth >= _MAX_CONNECTION_DEPTH:
        raise SwarmStoreError("Swarm storage exceeded its nested connection limit")
    if depth == 0:
        _CONNECTION_SLOTS.acquire()
    _CONNECTION_LOCAL.depth = depth + 1
    try:
        yield
    finally:
        _CONNECTION_LOCAL.depth = depth
        if depth == 0:
            _CONNECTION_SLOTS.release()


def _enable_wal(connection: sqlite3.Connection) -> None:
    """Negotiate the persistent mode during provisioning, before any transaction.

    Competing first-openers can receive SQLITE_BUSY immediately while upgrading
    a journal lock, even with a busy handler. Retry that negotiation with a fixed
    deadline and jitter; normal reads and writes never renegotiate journal mode.
    """
    deadline = time.monotonic() + 10
    connection.execute("PRAGMA busy_timeout=100")
    try:
        while True:
            try:
                mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                if mode != "wal":
                    mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if mode != "wal":
                    raise SwarmStoreError("Swarm storage requires SQLite WAL support")
                return
            except sqlite3.OperationalError as error:
                code = getattr(error, "sqlite_errorcode", 0) & 0xFF
                remaining = deadline - time.monotonic()
                if code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or remaining <= 0:
                    raise
                time.sleep(min(remaining, 0.005 + secrets.randbelow(20) / 1000))
    finally:
        connection.execute("PRAGMA busy_timeout=10000")


@contextmanager
def _connection(
    path: Path, *, write: bool = False, provision: bool = False, existing: bool = False
) -> Iterator[sqlite3.Connection]:
    with _connection_permit():
        connection = sqlite3.connect(
            path.as_uri() + "?mode=rw" if existing else path,
            uri=existing,
            timeout=10,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA foreign_keys=ON")
            if provision:
                _enable_wal(connection)
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class TeamRegistry:
    """Bounded team catalog; construction and an empty listing create no files."""

    def __init__(self, root: str | Path, *, clock: Callable[[], float] = time.time):
        self.root = Path(root).resolve()
        self.clock = clock

    def provision(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with _connection(self.root / "catalog.sqlite3", write=True, provision=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise SwarmStoreError("Swarm catalog requires a newer application version")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS teams (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                "request_key TEXT NOT NULL, name TEXT NOT NULL, created_at REAL NOT NULL, "
                "UNIQUE(owner,request_key))"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(teams)")}
            if "spec_hash" not in columns:
                connection.execute(
                    "ALTER TABLE teams ADD COLUMN spec_hash TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS teams_owner ON teams(owner,created_at DESC,id)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS team_cleanup (id TEXT PRIMARY KEY, "
                "owner TEXT NOT NULL, requested_at REAL NOT NULL, completed_at REAL)"
            )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def create(self, spec: TeamCreate, owner: str = "local-user") -> dict[str, Any]:
        if not owner or len(owner) > 200:
            raise ValueError("A bounded owner identity is required")
        spec = TeamCreate.model_validate(spec)
        if spec.mode != "local":
            raise SwarmStoreError("Distributed storage must be explicitly configured first")
        _validate_graph(spec.tasks, {})
        self.provision()
        with _connection(self.root / "catalog.sqlite3", write=True) as connection:
            existing = connection.execute(
                "SELECT id,spec_hash FROM teams WHERE owner=? AND request_key=?",
                (owner, spec.request_key),
            ).fetchone()
            if existing:
                store = TeamStore(
                    self.root / existing["id"] / "team.sqlite3", existing["id"], clock=self.clock
                )
                record = store.get()
                spec_hash = creation_fingerprint(spec)
                if (
                    (existing["spec_hash"] and existing["spec_hash"] != spec_hash)
                    or record["name"] != spec.name
                    or (not existing["spec_hash"] and record["goal"] != spec.goal)
                ):
                    raise SwarmConflictError("Creation key already identifies a different team")
                return record
            team_id = _id()
            store = TeamStore(self.root / team_id / "team.sqlite3", team_id, clock=self.clock)
            store.initialize(spec)
            connection.execute(
                "INSERT INTO teams VALUES (?,?,?,?,?,?)",
                (
                    team_id,
                    owner,
                    spec.request_key,
                    spec.name,
                    self.clock(),
                    creation_fingerprint(spec),
                ),
            )
        return store.get()

    def list(
        self,
        owner: str = "local-user",
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit, offset = _page(limit, offset)
        if not (self.root / "catalog.sqlite3").is_file():
            return []
        with _connection(self.root / "catalog.sqlite3") as connection:
            rows = connection.execute(
                "SELECT id,name,created_at FROM teams WHERE owner=? "
                "ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                (owner, limit, offset),
            ).fetchall()
        records = []
        for row in rows:
            try:
                records.append(self.open(row["id"], owner).get())
            except SwarmAccessError:
                # The catalog snapshot can legitimately precede a concurrent deletion.
                continue
            except (SwarmStoreError, sqlite3.DatabaseError, OSError) as exc:
                # Preserve the catalog page position without inventing team state.
                records.append(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "created_at": row["created_at"],
                        "available": False,
                        "error": f"Team storage requires recovery ({type(exc).__name__})",
                    }
                )
        return records

    @staticmethod
    def _validate_identity(team_id: str) -> None:
        if (
            not isinstance(team_id, str)
            or len(team_id) != 32
            or any(character not in "0123456789abcdef" for character in team_id)
        ):
            raise SwarmAccessError("Team unavailable for this owner")

    def _assert_visible(self, team_id: str, owner: str) -> None:
        self._validate_identity(team_id)
        if not (self.root / "catalog.sqlite3").is_file():
            raise SwarmAccessError("Team unavailable for this owner")
        with _connection(self.root / "catalog.sqlite3", existing=True) as connection:
            if not connection.execute(
                "SELECT 1 FROM teams WHERE id=? AND owner=?", (team_id, owner)
            ).fetchone():
                raise SwarmAccessError("Team unavailable for this owner")

    def open(self, team_id: str, owner: str = "local-user") -> TeamStore:
        self._assert_visible(team_id, owner)
        path = self.root / team_id / "team.sqlite3"
        if not path.is_file() or path.is_symlink() or path.resolve().parent != self.root / team_id:
            self._assert_visible(team_id, owner)
            raise SwarmStoreError(
                "Team storage is missing; restore its backup or retry provisioning"
            )
        store = TeamStore(path, team_id, clock=self.clock, registry=self, owner=owner)
        store.check_schema()
        return store

    def restore(
        self,
        backup: str | Path,
        owner: str = "local-user",
        *,
        request_key: str,
    ) -> dict[str, Any]:
        """Restore a verified snapshot under explicit ownership, without overwrites.

        Team and artifact identities survive. All active execution credentials
        and controller leases are fenced before the restored catalog is visible.
        """
        if not owner or len(owner) > 200 or not request_key or len(request_key) > 100:
            raise ValueError("Restore requires bounded owner and operation identities")
        source = Path(backup).resolve()
        manifest_path = source / "manifest.json"
        if not manifest_path.is_file() or manifest_path.stat().st_size > 4096:
            raise SwarmStoreError("Choose a complete Swarm backup with a bounded manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        team_id = manifest.get("team_id", "")
        if (
            not isinstance(team_id, str)
            or len(team_id) != 32
            or any(c not in "0123456789abcdef" for c in team_id)
        ):
            raise SwarmStoreError("Backup contains an invalid team identity")
        if manifest.get("schema_version") not in {1, SCHEMA_VERSION}:
            raise SwarmStoreError("Backup requires a compatible application version")
        database = source / "team.sqlite3"
        if database.is_symlink() or not database.is_file():
            raise SwarmStoreError("Backup database is missing or outside its namespace")
        original = TeamStore(database, team_id, clock=self.clock)
        with original._tx() as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise SwarmStoreError("Backup database failed its integrity check")
            team = original._team(connection)
            if team["version"] != manifest.get("team_version"):
                raise SwarmStoreError("Backup manifest and database versions disagree")
        self.provision()
        fingerprint = "backup:" + _digest(json.dumps(manifest, sort_keys=True))
        with _connection(self.root / "catalog.sqlite3", write=True) as catalog:
            existing = catalog.execute(
                "SELECT id,spec_hash FROM teams WHERE owner=? AND request_key=?",
                (owner, request_key),
            ).fetchone()
            if existing:
                if existing["id"] != team_id or existing["spec_hash"] != fingerprint:
                    raise SwarmConflictError("Restore key already identifies another backup")
                return TeamStore(
                    self.root / team_id / "team.sqlite3", team_id, clock=self.clock
                ).get()
            target = self.root / team_id
            if (
                target.exists()
                or catalog.execute("SELECT 1 FROM teams WHERE id=?", (team_id,)).fetchone()
                or catalog.execute("SELECT 1 FROM team_cleanup WHERE id=?", (team_id,)).fetchone()
            ):
                raise SwarmConflictError(
                    "Team already exists; restore into a separate instance to preserve current data"
                )
            with tempfile.TemporaryDirectory(prefix="restore-", dir=self.root) as temporary:
                staging = Path(temporary)
                staged_database = staging / "team.sqlite3"
                with (
                    _connection_permit(),
                    closing(
                        sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                    ) as source_connection,
                    _connection_permit(),
                    closing(sqlite3.connect(staged_database)) as target_connection,
                ):
                    source_connection.backup(target_connection, pages=128)
                restored = TeamStore(staged_database, team_id, clock=self.clock)
                restored.check_schema()
                (staging / "objects").mkdir()
                with restored._tx(write=True) as connection, original._tx() as source_connection:
                    rows = connection.execute("SELECT id FROM artifacts").fetchall()
                    if str(len(rows)) != str(manifest.get("artifacts")):
                        raise SwarmStoreError(
                            "Backup artifact inventory does not match its manifest"
                        )
                    for row in rows:
                        record, _ = original._artifact_content(source_connection, row[0])
                        shutil.copyfile(
                            source / "objects" / record["object_key"],
                            staging / "objects" / record["object_key"],
                        )
                    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                        raise SwarmStoreError(
                            "Backup contains broken provenance or approval references"
                        )
                    restored._suspend_attempts(connection, "Restored from consistent backup")
                    connection.execute(
                        "UPDATE controller SET expires_at=0,fence=fence+1 WHERE singleton=1"
                    )
                    restored._event(
                        connection,
                        "storage.restored",
                        "Team restored; execution credentials fenced",
                    )
                target.mkdir()
                for entry in staging.iterdir():
                    shutil.move(str(entry), str(target / entry.name))
            catalog.execute(
                "INSERT INTO teams VALUES (?,?,?,?,?,?)",
                (team_id, owner, request_key, team["name"], self.clock(), fingerprint),
            )
        return self.open(team_id, owner).get()

    def delete(self, team_id: str, owner: str = "local-user") -> dict[str, Any]:
        """Durably hide one inactive team, then retry its owner-authorized cleanup.

        The tombstone survives partial removal and process crashes. Completed
        tombstones retain retry authority and prevent an old cleanup request
        from deleting a restored namespace. Publications are outside this tree.
        """
        self._validate_identity(team_id)
        if not (self.root / "catalog.sqlite3").is_file():
            raise SwarmAccessError("Team unavailable for this owner")
        self.provision()
        with _connection(self.root / "catalog.sqlite3", write=True) as catalog:
            cleanup = catalog.execute(
                "SELECT owner,completed_at FROM team_cleanup WHERE id=?", (team_id,)
            ).fetchone()
            if cleanup:
                if cleanup["owner"] != owner:
                    raise SwarmAccessError("Team unavailable for this owner")
                if cleanup["completed_at"] is not None:
                    return {"team_id": team_id, "deleted": True}
            else:
                store = self.open(team_id, owner)
                with store._tx(write=True) as connection:
                    team = store._team(connection)
                    if team["state"] not in _TERMINAL:
                        raise SwarmConflictError(
                            "Archive or terminate the team before deleting its workspace"
                        )
                    if connection.execute(
                        "SELECT 1 FROM publications WHERE state='pending' LIMIT 1"
                    ).fetchone():
                        raise SwarmConflictError(
                            "Finish or revoke pending publications before deleting this team"
                        )
                    self._cleanup_directory(team_id)
                    catalog.execute(
                        "INSERT INTO team_cleanup VALUES (?,?,?,NULL)",
                        (team_id, owner, self.clock()),
                    )
                    catalog.execute("DELETE FROM teams WHERE id=? AND owner=?", (team_id, owner))
                    # Fence new writers while still holding the team write lock.
                    # A writer admitted earlier rechecks visibility after acquiring it.
                    catalog.commit()
        # Commit visibility first: WAL readers do not block a catalog writer,
        # and cached TeamStore handles must stop admitting fresh transactions.
        deadline = time.monotonic() + _CLEANUP_TIMEOUT
        while True:
            directory = self._cleanup_directory(team_id)
            if not directory.exists():
                break
            try:
                shutil.rmtree(directory)
                break
            except OSError as error:
                remaining = deadline - time.monotonic()
                transient = error.errno in {
                    errno.EACCES,
                    errno.EPERM,
                    errno.EBUSY,
                    errno.ENOTEMPTY,
                    errno.ENOENT,
                } or getattr(error, "winerror", None) in {32, 33}
                if not transient or remaining <= 0:
                    raise SwarmStoreError(
                        "Team is hidden; workspace cleanup is pending. Retry deletion after "
                        "existing readers release their files."
                    ) from error
                time.sleep(min(remaining, 0.01 + secrets.randbelow(40) / 1000))
        with _connection(self.root / "catalog.sqlite3", write=True) as catalog:
            catalog.execute(
                "UPDATE team_cleanup SET completed_at=? WHERE id=? AND owner=?",
                (self.clock(), team_id, owner),
            )
        return {"team_id": team_id, "deleted": True}

    def _cleanup_directory(self, team_id: str) -> Path:
        """Validate the exact namespace again before every physical cleanup attempt."""
        directory = self.root / team_id
        if directory.is_symlink() or not _same_resolved_path(directory, directory.resolve()):
            raise SwarmAccessError("Team deletion target escaped the instance workspace")
        if directory.exists() and not directory.is_dir():
            raise SwarmAccessError("Team deletion target is not a workspace directory")
        for path in directory.rglob("*"):
            if path.is_symlink() or not _same_resolved_path(path, path.resolve()):
                raise SwarmAccessError("Team deletion refuses linked paths in its workspace")
        return directory


def _validate_graph(specs: list[TaskSpec], existing: dict[str, list[str]]) -> None:
    graph = dict(existing)
    for spec in specs:
        if spec.id in graph:
            raise SwarmConflictError(f"Task {spec.id} already exists")
        graph[spec.id] = list(spec.dependencies)
    if any(
        dependency not in graph for dependencies in graph.values() for dependency in dependencies
    ):
        raise ValueError("Every dependency must name a task in this team")
    visiting: set[str] = set()
    visited: set[str] = set()
    # Iterative DFS keeps a valid large DAG independent of Python recursion limits.
    for root in graph:
        stack = [(root, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                visiting.remove(node)
                visited.add(node)
            elif node in visiting:
                raise ValueError("Task dependencies contain a cycle")
            elif node not in visited:
                visiting.add(node)
                stack.append((node, True))
                stack.extend((dependency, False) for dependency in graph[node])


class TeamStore(MailboxMixin, ObjectMixin):
    """Trusted owner handle. Never expose this object in a worker tool set."""

    def __init__(
        self,
        path: str | Path,
        team_id: str,
        *,
        clock: Callable[[], float] = time.time,
        registry: TeamRegistry | None = None,
        owner: str = "local-user",
    ):
        self.path = Path(path)
        self.team_id = team_id
        self.clock = clock
        self._registry = registry
        self._owner = owner

    @contextmanager
    def _tx(self, *, write: bool = False, provision: bool = False) -> Iterator[sqlite3.Connection]:
        if self._registry is not None:
            self._registry._assert_visible(self.team_id, self._owner)
        try:
            with _connection(
                self.path, write=write, provision=provision, existing=self._registry is not None
            ) as connection:
                if write and self._registry is not None:
                    self._registry._assert_visible(self.team_id, self._owner)
                yield connection
        except sqlite3.Error:
            if self._registry is not None:
                # Normalize a deletion between the visibility check and open;
                # mode=rw ensures that a stale handle cannot recreate storage.
                self._registry._assert_visible(self.team_id, self._owner)
            raise

    def check_schema(self) -> None:
        with self._tx(write=True, provision=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {1, SCHEMA_VERSION}:
                raise SwarmStoreError(
                    "Unsupported team schema; update the app or restore a compatible backup"
                )
            self._team(connection)
            if version < SCHEMA_VERSION:
                _apply_schema(connection)
                team = self._team(connection)
                team.setdefault("acceptance", team["goal"])
                team.setdefault("network_bytes", "0")
                self._save_team(connection, team)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self._event(
                    connection,
                    "storage.migrated",
                    "Team storage schema migrated",
                    data={"from": version, "to": SCHEMA_VERSION},
                )

    def initialize(self, spec: TeamCreate) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _connection(self.path, write=True, provision=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise SwarmStoreError("Team storage requires a newer application version")
            _apply_schema(connection)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            if connection.execute("SELECT 1 FROM team").fetchone():
                self._team(connection)
                return
            now = self.clock()
            lead_id = _id()
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
                mode=spec.mode,
            ).model_dump(mode="json")
            connection.execute("INSERT INTO team VALUES (1,?)", (_json(record),))
            self._insert_agent(connection, lead_id, "Team lead", "lead", "general", "coordination")
            self._insert_tasks(connection, spec.tasks)
            if spec.preparation_required:
                from .preparation import initialize

                initialize(self, connection, spec)
            self._event(connection, "team.created", "Team created")

    def _team(self, connection: sqlite3.Connection) -> dict[str, Any]:
        row = connection.execute("SELECT record FROM team WHERE singleton=1").fetchone()
        if row is None:
            raise SwarmStoreError("Team provisioning is incomplete; retry creation")
        record: dict[str, Any] = json.loads(row[0])
        if record["id"] != self.team_id:
            raise SwarmAccessError("Team identity does not match its storage")
        return record

    def _save_team(self, connection: sqlite3.Connection, record: dict[str, Any]) -> None:
        record["updated_at"] = self.clock()
        connection.execute("UPDATE team SET record=? WHERE singleton=1", (_json(record),))

    def get(self) -> dict[str, Any]:
        with self._tx() as connection:
            return self._team(connection)

    def _controller(
        self,
        connection: sqlite3.Connection,
        controller: SwarmController,
        *,
        now: float | None = None,
    ) -> sqlite3.Row:
        if not isinstance(controller, SwarmController) or controller.team_id != self.team_id:
            raise SwarmAccessError("Trusted controller authority required")
        row = connection.execute("SELECT * FROM controller WHERE singleton=1").fetchone()
        if (
            row is None
            or row["instance_id"] != controller.instance_id
            or row["fence"] != controller.fence
            or not hmac.compare_digest(row["token_hash"], _digest(controller.token))
            or row["expires_at"] <= (self.clock() if now is None else now)
        ):
            raise SwarmAccessError("Controller lease expired or was fenced")
        return row

    def _actor(
        self,
        connection: sqlite3.Connection,
        actor: SwarmActor,
        *,
        writing: bool = False,
    ) -> sqlite3.Row:
        if not isinstance(actor, SwarmActor) or actor.team_id != self.team_id:
            raise SwarmAccessError("Scoped team membership required")
        row = connection.execute("SELECT * FROM agents WHERE id=?", (actor.agent_id,)).fetchone()
        if (
            row is None
            or not row["active"]
            or not hmac.compare_digest(row["token_hash"], _actor_digest(actor))
        ):
            raise SwarmAccessError("Team membership missing or revoked")
        team = self._team(connection)
        if team["state"] in _TERMINAL | {"paused"} or (writing and team["state"] != "running"):
            raise SwarmAccessError("Team lifecycle does not permit this worker operation")
        if (
            writing
            and team["started_at"] is not None
            and self.clock() >= team["started_at"] + team["limits"]["runtime_seconds"]
        ):
            raise SwarmBudgetError("The team's authorized runtime has expired")
        if actor.task_id:
            attempt = connection.execute(
                "SELECT a.*,c.fence AS current_fence,c.expires_at FROM attempts a "
                "JOIN controller c ON c.singleton=1 WHERE a.task_id=? AND a.fence=? AND "
                "a.agent_id=?",
                (actor.task_id, actor.task_fence, actor.agent_id),
            ).fetchone()
            if (
                attempt is None
                or attempt["state"] != "running"
                or attempt["controller_fence"] != attempt["current_fence"]
                or attempt["expires_at"] <= self.clock()
                or attempt["heartbeat_at"] + 90 <= self.clock()
            ):
                raise SwarmAccessError("Task attempt expired or was fenced")
        return row

    def _require_tool(self, connection: sqlite3.Connection, name: str) -> None:
        if name not in self._team(connection)["policy"]["tools"]:
            raise SwarmAccessError("Team capability policy does not permit this operation")

    def validate_actor(self, actor: SwarmActor, *, writing: bool = False) -> None:
        with self._tx() as connection:
            self._actor(connection, actor, writing=writing)

    def acquire_controller(
        self,
        instance_id: str,
        now: float | None = None,
        ttl: float = 90,
    ) -> SwarmController | None:
        if not instance_id or not 1 <= ttl <= 300:
            raise ValueError("A controller identity and lease from 1 to 300 seconds are required")
        now = self.clock() if now is None else now
        with self._tx(write=True) as connection:
            row = connection.execute("SELECT * FROM controller WHERE singleton=1").fetchone()
            if row and row["expires_at"] > now:
                return None
            fence = row["fence"] + 1 if row else 1
            token = secrets.token_urlsafe(32)
            connection.execute(
                "INSERT INTO controller VALUES (1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET "
                "instance_id=excluded.instance_id,fence=excluded.fence,token_hash=excluded.tok"
                "en_hash,expires_at=excluded.expires_at",
                (instance_id, fence, _digest(token), now + ttl),
            )
            self._event(
                connection,
                "controller.acquired",
                "Controller lease acquired",
                data={"fence": fence},
            )
            return SwarmController(self.team_id, instance_id, fence, token)

    def renew_controller(
        self,
        controller: SwarmController,
        now: float | None = None,
        ttl: float = 90,
    ) -> bool:
        if not 1 <= ttl <= 300:
            raise ValueError("Lease must be from 1 to 300 seconds")
        now = self.clock() if now is None else now
        with self._tx(write=True) as connection:
            self._controller(connection, controller, now=now)
            connection.execute("UPDATE controller SET expires_at=? WHERE singleton=1", (now + ttl,))
        return True

    def release_controller(self, controller: SwarmController, requeue: bool = True) -> None:
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            if requeue:
                self._suspend_attempts(connection, "Controller released")
            connection.execute("UPDATE controller SET expires_at=0 WHERE singleton=1")

    def _suspend_attempts(self, connection: sqlite3.Connection, reason: str) -> None:
        for row in connection.execute("SELECT record FROM tasks WHERE state='running'").fetchall():
            task = json.loads(row[0])
            connection.execute(
                "UPDATE attempts SET state='interrupted',finished_at=?,reason=? "
                "WHERE task_id=? AND fence=? AND state='running'",
                (self.clock(), reason, task["id"], task["fence"]),
            )
            self._idle_agent(connection, task["owner_id"])
            task.update(
                state=TaskState.READY,
                owner_id=None,
                fence=task["fence"] + 1,
                attempt_count=max(0, task["attempt_count"] - 1),
                reason=reason,
            )
            self._save_task(connection, task)
        # Rotate every credential, including unbound handles; logical identities survive.
        for row in connection.execute("SELECT id FROM agents WHERE active=1").fetchall():
            connection.execute(
                "UPDATE agents SET token_hash=? WHERE id=?",
                (_digest(secrets.token_urlsafe(32)), row[0]),
            )

    def user_transition(
        self,
        target: str,
        expected_version: int | None = None,
        reason: str = "",
        expected_storage_generation: str | None = None,
    ) -> dict[str, Any]:
        """Authenticated registry-owner control; never installed in worker tools."""
        return self.apply_user_control(
            target, expected_version, reason, expected_storage_generation
        )[0]

    def apply_user_control(
        self,
        target: str,
        expected_version: int | None = None,
        reason: str = "",
        expected_storage_generation: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Report an atomic state change so duplicate controls never cancel live work."""
        with self._tx(write=True) as connection:
            before = self._team(connection)["version"]
            result = self._transition(
                connection, target, expected_version, reason, expected_storage_generation
            )
            changed = before != result["version"]
            if changed and target in {"paused", "canceled", "archived"}:
                connection.execute("UPDATE controller SET expires_at=0 WHERE singleton=1")
            return result, changed

    def transition(
        self,
        controller: SwarmController,
        target: str,
        expected_version: int | None = None,
        reason: str = "",
        expected_storage_generation: str | None = None,
    ) -> dict[str, Any]:
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            return self._transition(
                connection, target, expected_version, reason, expected_storage_generation
            )

    def _transition(
        self,
        connection: sqlite3.Connection,
        target: str,
        expected_version: int | None,
        reason: str,
        expected_storage_generation: str | None = None,
    ) -> dict[str, Any]:
        team = self._team(connection)
        _check_expected(team, expected_version, expected_storage_generation)
        preparation = team.get("checkpoint", {}).get("preparation", {})
        if (
            target == "running"
            and preparation.get("required")
            and preparation.get("state") != "launched"
        ):
            raise SwarmConflictError("Review and explicitly launch the prepared plan first")
        if team["state"] == target:
            return team
        if target not in _TRANSITIONS[team["state"]]:
            raise SwarmConflictError(f"Cannot transition {team['state']} to {target}")
        if (
            target == "succeeded"
            and connection.execute(
                "SELECT 1 FROM tasks WHERE state<>'succeeded' LIMIT 1"
            ).fetchone()
        ):
            raise SwarmConflictError("Every task must be accepted before team success")
        if (
            target == "succeeded"
            and not connection.execute("SELECT 1 FROM tasks LIMIT 1").fetchone()
        ):
            raise SwarmConflictError("Team success requires at least one accepted task")
        team.update(state=target, version=team["version"] + 1, reason=reason[:2000])
        if target == "running" and team["started_at"] is None:
            team["started_at"] = self.clock()
        if target in {"paused", "blocked"}:
            self._suspend_attempts(connection, reason or "Team paused")
        if target in _TERMINAL:
            for row in connection.execute(
                "SELECT record FROM tasks WHERE state IN ('ready','running','blocked')"
            ).fetchall():
                task = json.loads(row[0])
                task.update(state="canceled", fence=task["fence"] + 1, reason=reason or target)
                self._save_task(connection, task)
            connection.execute(
                "UPDATE attempts SET state='canceled',finished_at=?,reason=? WHERE state='running'",
                (self.clock(), reason or target),
            )
            for row in connection.execute("SELECT record FROM agents").fetchall():
                agent = json.loads(row[0])
                agent.update(state="stopped", task_id=None, tool_activity=None)
                self._save_agent(connection, agent)
            # The lead remains a logical publication principal; worker execution
            # and all task-scoped grants are disabled by terminal lifecycle.
            connection.execute("UPDATE agents SET active=0 WHERE role<>'lead'")
        self._save_team(connection, team)
        self._event(connection, "team." + target, reason or f"Team {target}")
        return team

    def checkpoint(
        self,
        controller: SwarmController,
        checkpoint: dict[str, Any],
        expected_version: int | None = None,
        *,
        request_key: str | None = None,
    ) -> dict[str, Any]:
        if len(_json(checkpoint).encode("utf-8")) > 16_384:
            raise ValueError("Checkpoint exceeds 16 KiB")
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            team = self._team(connection)
            return self._checkpoint(
                connection,
                team,
                checkpoint,
                source="controller",
                expected_version=expected_version,
                request_key=request_key,
            )

    def _checkpoint(
        self,
        connection: Any,
        team: dict[str, Any],
        checkpoint: dict[str, Any],
        *,
        source: str,
        expected_version: int | None = None,
        request_key: str | None = None,
    ) -> dict[str, Any]:
        from .checkpoints import save

        return save(
            self,
            connection,
            team,
            checkpoint,
            source=source,
            expected_version=expected_version,
            request_key=request_key,
        )

    def _insert_agent(
        self,
        connection: sqlite3.Connection,
        agent_id: str,
        name: str,
        role: str,
        domain: str,
        group_id: str,
    ) -> SwarmActor:
        token = secrets.token_urlsafe(32)
        record = AgentRecord(
            id=agent_id,
            team_id=self.team_id,
            name=name[:120],
            role=AgentRole(role),
            domain=domain[:80],
            group_id=group_id[:100],
        ).model_dump(mode="json")
        connection.execute(
            "INSERT INTO agents VALUES (?,?,?,1,?,?)",
            (agent_id, role, record["state"], _digest(token), _json(record)),
        )
        return SwarmActor(self.team_id, agent_id, token)

    def add_agent(
        self,
        controller: SwarmController,
        name: str,
        role: str = "worker",
        domain: str = "general",
        group_id: str = "delivery",
        agent_id: str | None = None,
    ) -> SwarmActor:
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            return self._add_agent(connection, name, role, domain, group_id, agent_id)

    def _add_agent(
        self,
        connection: Any,
        name: str,
        role: str = "worker",
        domain: str = "general",
        group_id: str = "delivery",
        agent_id: str | None = None,
    ) -> SwarmActor:
        if role not in {"worker", "coordinator"}:
            raise SwarmAccessError("The persistent team lead cannot be replaced by adding an agent")
        team = self._team(connection)
        if team["state"] in _TERMINAL:
            raise SwarmConflictError("Terminal teams cannot add workers")
        count = connection.execute("SELECT count(*) FROM agents WHERE role<>'lead'").fetchone()[0]
        if count >= int(team["limits"]["worker_limit"]):
            raise SwarmBudgetError("Total logical worker limit reached")
        actor = self._insert_agent(connection, agent_id or _id(), name, role, domain, group_id)
        self._event(connection, "agent.created", f"{role.title()} created", agent_id=actor.agent_id)
        return actor

    def actor_for(self, controller: SwarmController, agent_id: str) -> SwarmActor:
        """Rotate a membership credential without replacing its logical identity."""
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            row = connection.execute(
                "SELECT * FROM agents WHERE id=? AND active=1", (agent_id,)
            ).fetchone()
            if row is None:
                raise SwarmAccessError("Active team member required")
            token = secrets.token_urlsafe(32)
            connection.execute(
                "UPDATE agents SET token_hash=? WHERE id=?", (_digest(token), agent_id)
            )
            return SwarmActor(self.team_id, agent_id, token)

    def revoke_member(self, controller: SwarmController, agent_id: str) -> None:
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            if agent_id == self._team(connection)["lead_id"]:
                raise SwarmConflictError("Use team termination to stop its persistent lead")
            for row in connection.execute(
                "SELECT record FROM tasks WHERE owner_id=? AND state='running'", (agent_id,)
            ).fetchall():
                task = json.loads(row[0])
                self._fail_task(connection, task, "Membership revoked", retry=True)
            connection.execute("UPDATE agents SET active=0 WHERE id=?", (agent_id,))
            row = connection.execute("SELECT record FROM agents WHERE id=?", (agent_id,)).fetchone()
            if row:
                agent = json.loads(row[0])
                agent.update(state="stopped", task_id=None, tool_activity=None)
                self._save_agent(connection, agent)
            self._event(connection, "agent.revoked", "Membership revoked", agent_id=agent_id)

    def _save_agent(self, connection: sqlite3.Connection, agent: dict[str, Any]) -> None:
        connection.execute(
            "UPDATE agents SET state=?,record=? WHERE id=?",
            (agent["state"], _json(agent), agent["id"]),
        )

    def _insert_tasks(
        self, connection: sqlite3.Connection, specs: list[TaskSpec]
    ) -> list[dict[str, Any]]:
        records = []
        now = self.clock()
        for spec in specs:
            record = TaskRecord(
                **spec.model_dump(),
                team_id=self.team_id,
                state=TaskState.READY,
                created_at=now,
                updated_at=now,
            ).model_dump(mode="json")
            connection.execute(
                "INSERT INTO tasks VALUES (?, 'ready', NULL, 0, ?)", (spec.id, _json(record))
            )
            records.append(record)
        for spec in specs:
            connection.executemany(
                "INSERT INTO dependencies VALUES (?,?)",
                [(spec.id, dependency) for dependency in spec.dependencies],
            )
        return records

    def add_tasks(self, controller: SwarmController, specs: list[TaskSpec]) -> list[dict[str, Any]]:
        specs = [TaskSpec.model_validate(spec) for spec in specs]
        if not 1 <= len(specs) <= 1000:
            raise ValueError("Add between 1 and 1000 tasks per operation")
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            if self._team(connection)["state"] in _TERMINAL:
                raise SwarmConflictError("Terminal team cannot add tasks")
            existing = {
                row["id"]: json.loads(row["record"])["dependencies"]
                for row in connection.execute("SELECT id,record FROM tasks")
            }
            _validate_graph(specs, existing)
            records = self._insert_tasks(connection, specs)
            self._event(
                connection, "tasks.added", "Task graph extended", data={"count": str(len(records))}
            )
            return records

    def _task(self, connection: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT record FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise SwarmAccessError("Task unavailable in this team")
        return json.loads(row[0])

    def _save_task(self, connection: sqlite3.Connection, task: dict[str, Any]) -> None:
        task.update(version=task["version"] + 1, updated_at=self.clock())
        connection.execute(
            "UPDATE tasks SET state=?,owner_id=?,fence=?,record=? WHERE id=?",
            (task["state"], task["owner_id"], task["fence"], _json(task), task["id"]),
        )

    def read_task(self, actor: SwarmActor, task_id: str) -> dict[str, Any]:
        with self._tx() as connection:
            self._actor(connection, actor)
            return self._task(connection, task_id)

    def ready_tasks(self, limit: int = 32) -> list[dict[str, Any]]:
        _page(limit)
        with self._tx() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT t.record FROM tasks t WHERE t.state='ready' AND NOT EXISTS "
                    "(SELECT 1 FROM dependencies d JOIN tasks p ON p.id=d.depends_on "
                    "WHERE d.task_id=t.id AND p.state<>'succeeded') "
                    "ORDER BY json_extract(t.record,'$.priority') "
                    "DESC,json_extract(t.record,'$.created_at'),t.id LIMIT ?",
                    (limit,),
                )
            ]

    def agents_for(
        self,
        domain: str,
        required_tools: list[str] | None = None,
        limit: int = 32,
    ) -> list[dict[str, Any]]:
        """Rank only eligible idle workers by this domain's posterior upper estimate.

        A quarter standard-deviation bonus exposes uncertainty to the scheduler:
        equally likely novices get exploration, while unrelated wins cannot hide
        known domain failures. Capability, concurrency and budget checks remain
        independent authority checks in claim/reserve, including explicit assignment.
        """
        _page(limit)
        with self._tx() as connection:
            return self._agents_for(connection, domain, required_tools, limit)

    def _agents_for(
        self, connection: Any, domain: str, required_tools: list[str] | None, limit: int
    ) -> list[dict[str, Any]]:
        if not set(required_tools or []) <= set(self._team(connection)["policy"]["tools"]):
            return []
        return [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT a.record FROM agents a LEFT JOIN reputation r "
                "ON r.agent_id=a.id AND r.domain=? "
                "WHERE a.active=1 AND a.state='idle' AND a.role='worker' "
                "AND json_extract(a.record,'$.domain') IN (?, 'general') "
                "ORDER BY (coalesce(CAST(json_extract(r.record,'$.reliability') AS REAL),0.5) "
                "+ 0.25*coalesce(CAST(json_extract(r.record,'$.uncertainty') AS REAL),"
                "0.22360679774997896)) DESC,a.id LIMIT ?",
                (domain, domain, limit),
            )
        ]

    def claim(
        self,
        controller: SwarmController,
        agent_id: str,
        task_id: str,
        now: float | None = None,
    ) -> SwarmActor:
        now = self.clock() if now is None else now
        with self._tx(write=True) as connection:
            return self._claim(connection, controller, agent_id, task_id, now)

    def _claim(
        self,
        connection: Any,
        controller: SwarmController,
        agent_id: str,
        task_id: str,
        now: float,
    ) -> SwarmActor:
        self._controller(connection, controller, now=now)
        team = self._team(connection)
        if team["state"] != "running":
            raise SwarmConflictError("Only a running team can dispatch")
        if (
            team["started_at"] is not None
            and now >= team["started_at"] + team["limits"]["runtime_seconds"]
        ):
            raise SwarmBudgetError("Team runtime limit reached")
        running = connection.execute(
            "SELECT count(*) FROM attempts WHERE state='running'"
        ).fetchone()[0]
        if running >= team["limits"]["concurrency"]:
            raise SwarmBudgetError("Team concurrency limit reached")
        task = self._task(connection, task_id)
        if task["state"] != "ready" or task["attempt_count"] >= team["limits"]["max_attempts"]:
            raise SwarmConflictError("Task is not eligible for another attempt")
        if not set(task["required_tools"]) <= set(team["policy"]["tools"]):
            raise SwarmAccessError("Task requires tools outside the team capability policy")
        if connection.execute(
            "SELECT 1 FROM dependencies d JOIN tasks p ON p.id=d.depends_on "
            "WHERE d.task_id=? AND p.state<>'succeeded' LIMIT 1",
            (task_id,),
        ).fetchone():
            raise SwarmConflictError("Task dependencies are not accepted")
        row = connection.execute(
            "SELECT * FROM agents WHERE id=? AND active=1 AND state='idle'", (agent_id,)
        ).fetchone()
        if row is None:
            raise SwarmAccessError("An idle active team member is required")
        from .control_requests import validate_assignment

        validate_assignment(self, connection, task, agent_id)
        token = secrets.token_urlsafe(32)
        fence = task["fence"] + 1
        task.update(
            state="running",
            owner_id=agent_id,
            fence=fence,
            attempt_count=task["attempt_count"] + 1,
        )
        self._save_task(connection, task)
        agent = json.loads(row["record"])
        agent.update(state="running", task_id=task_id)
        self._save_agent(connection, agent)
        connection.execute(
            "UPDATE agents SET token_hash=? WHERE id=?",
            (_digest(token + f"|{task_id}:{fence}"), agent_id),
        )
        connection.execute(
            "INSERT INTO attempts "
            "(id,task_id,agent_id,fence,controller_fence,state,heartbeat_at,started_at) "
            "VALUES (?,?,?,?,?,'running',?,?)",
            (_id(), task_id, agent_id, fence, controller.fence, now, now),
        )
        self._event(
            connection,
            "task.claimed",
            task["title"],
            agent_id=agent_id,
            task_id=task_id,
            data={"fence": fence},
        )
        return SwarmActor(self.team_id, agent_id, token, task_id, fence)

    def claim_batch(
        self,
        controller: SwarmController,
        *,
        limit: int,
        worker_slots: int,
        ready_offset: int = 0,
        source_ids: frozenset[str] = frozenset(),
        excluded_tasks: frozenset[str] = frozenset(),
    ) -> DispatchBatch:
        """Claim at most eight tasks with the same local/PostgreSQL authority checks."""
        from .dispatch import claim_batch

        return claim_batch(
            self,
            controller,
            limit=limit,
            worker_slots=worker_slots,
            ready_offset=ready_offset,
            source_ids=source_ids,
            excluded_tasks=excluded_tasks,
        )

    def heartbeat(self, actor: SwarmActor, now: float | None = None) -> bool:
        now = self.clock() if now is None else now
        with self._tx(write=True) as connection:
            self._actor(connection, actor)
            if not actor.task_id:
                raise SwarmAccessError("Task-scoped attempt required")
            connection.execute(
                "UPDATE attempts SET heartbeat_at=? WHERE task_id=? AND fence=?",
                (now, actor.task_id, actor.task_fence),
            )
        return True

    def _idle_agent(self, connection: sqlite3.Connection, agent_id: str) -> None:
        row = connection.execute("SELECT record FROM agents WHERE id=?", (agent_id,)).fetchone()
        if row:
            agent = json.loads(row[0])
            agent.update(state="idle", task_id=None, tool_activity=None)
            self._save_agent(connection, agent)

    def _fail_task(
        self, connection: sqlite3.Connection, task: dict[str, Any], error: str, *, retry: bool
    ) -> None:
        connection.execute(
            "UPDATE attempts SET state='failed',finished_at=?,reason=? WHERE task_id=? AND "
            "fence=? AND state='running'",
            (self.clock(), error[:2000], task["id"], task["fence"]),
        )
        self._idle_agent(connection, task["owner_id"])
        maximum = self._team(connection)["limits"]["max_attempts"]
        task.update(
            state="ready" if retry and task["attempt_count"] < maximum else "failed",
            owner_id=None,
            fence=task["fence"] + 1,
            reason=error[:2000],
        )
        self._save_task(connection, task)
        self._event(connection, "task." + task["state"], error[:500], task_id=task["id"])
        if task["state"] == "failed":
            # Propagate unavailable dependencies without inventing accepted results.
            pending = [task["id"]]
            while pending:
                failed_id = pending.pop()
                for row in connection.execute(
                    "SELECT t.record FROM tasks t JOIN dependencies d ON d.task_id=t.id WHERE "
                    "d.depends_on=? AND t.state='ready'",
                    (failed_id,),
                ).fetchall():
                    dependent = json.loads(row[0])
                    dependent.update(state="blocked", reason=f"Dependency {failed_id} failed")
                    self._save_task(connection, dependent)
                    pending.append(dependent["id"])

    def fail(
        self, actor: SwarmActor, error: str, retry: bool = True, *, controller: SwarmController
    ) -> dict[str, Any]:
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            self._actor(connection, actor)
            task = self._task(connection, actor.task_id)
            self._fail_task(connection, task, error, retry=retry)
            return task

    def recover(
        self, controller: SwarmController, now: float | None = None
    ) -> list[dict[str, Any]]:
        now = self.clock() if now is None else now
        with self._tx(write=True) as connection:
            self._controller(connection, controller, now=now)
            stale = connection.execute(
                "SELECT t.record FROM tasks t JOIN attempts a ON a.task_id=t.id AND "
                "a.fence=t.fence "
                "WHERE a.state='running' AND (a.heartbeat_at<=? OR a.controller_fence<>?)",
                (now - 90, controller.fence),
            ).fetchall()
            tasks = [json.loads(row[0]) for row in stale]
            for task in tasks:
                self._fail_task(
                    connection, task, "Attempt lease expired or controller was replaced", retry=True
                )
            return tasks

    def reserve(
        self,
        actor: SwarmActor,
        request_key: str,
        tokens: str | int,
        cost_microusd: str | int = "0",
    ) -> dict[str, Any]:
        tokens, cost_microusd = decimal_counter(tokens), decimal_counter(cost_microusd)
        if not request_key or len(request_key) > 100:
            raise ValueError("A bounded invocation idempotency key is required")
        with self._tx(write=True) as connection:
            self._actor(connection, actor, writing=True)
            if not actor.task_id:
                raise SwarmAccessError("Invocation must belong to a fenced task attempt")
            return self._reserve_in(connection, actor, request_key, tokens, cost_microusd)

    def _reserve_in(
        self,
        connection: Any,
        actor: SwarmActor,
        request_key: str,
        tokens: str,
        cost_microusd: str,
    ) -> dict[str, Any]:
        """Shared arithmetic after the caller validates its distinct authority."""
        existing = connection.execute(
            "SELECT * FROM reservations WHERE agent_id=? AND request_key=?",
            (actor.agent_id, request_key),
        ).fetchone()
        if existing:
            if (
                existing["tokens"],
                existing["cost_microusd"],
                existing["task_id"],
                existing["task_fence"],
            ) != (tokens, cost_microusd, actor.task_id, actor.task_fence):
                raise SwarmConflictError("Invocation key already reserves different work")
            return dict(existing)
        team = self._team(connection)
        new_tokens = int(team["tokens_reserved"]) + int(tokens)
        new_cost = int(team["cost_reserved_microusd"]) + int(cost_microusd)
        if int(team["tokens_used"]) + new_tokens > int(team["limits"]["token_budget"]):
            raise SwarmBudgetError("Token budget exhausted")
        monetary_limit = team["limits"]["monetary_limit_microusd"]
        if monetary_limit is not None and int(team["cost_microusd"]) + new_cost > int(
            monetary_limit
        ):
            raise SwarmBudgetError("Monetary budget exhausted")
        lease = connection.execute("SELECT fence FROM controller WHERE singleton=1").fetchone()
        reservation_id = _id()
        connection.execute(
            "INSERT INTO reservations "
            "(id,request_key,agent_id,task_id,task_fence,controller_fence,tokens,cost_micr"
            "ousd,state,created_at) VALUES (?,?,?,?,?,?,?,?,'reserved',?)",
            (
                reservation_id,
                request_key,
                actor.agent_id,
                actor.task_id,
                actor.task_fence,
                lease[0],
                tokens,
                cost_microusd,
                self.clock(),
            ),
        )
        team.update(tokens_reserved=str(new_tokens), cost_reserved_microusd=str(new_cost))
        self._save_team(connection, team)
        return dict(
            connection.execute(
                "SELECT * FROM reservations WHERE id=?", (reservation_id,)
            ).fetchone()
        )

    def reconcile(
        self,
        controller: SwarmController,
        reservation_id: str,
        tokens: str | int | None = None,
        cost_microusd: str | int | None = None,
    ) -> dict[str, Any]:
        if tokens is not None:
            tokens = decimal_counter(tokens)
        if cost_microusd is not None:
            cost_microusd = decimal_counter(cost_microusd)
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            return self._reconcile_in(connection, reservation_id, tokens, cost_microusd)

    def _reconcile_in(
        self,
        connection: Any,
        reservation_id: str,
        tokens: str | None,
        cost_microusd: str | None,
    ) -> dict[str, Any]:
        """Shared exactly-once settlement after caller-specific fencing."""
        row = connection.execute(
            "SELECT * FROM reservations WHERE id=?", (reservation_id,)
        ).fetchone()
        if row is None:
            raise SwarmAccessError("Reservation unavailable in this team")
        team = self._team(connection)
        overrun = False
        for supplied, actual_key, reserved_key, used_key, reservation_key in (
            (tokens, "actual_tokens", "tokens_reserved", "tokens_used", "tokens"),
            (
                cost_microusd,
                "actual_cost_microusd",
                "cost_reserved_microusd",
                "cost_microusd",
                "cost_microusd",
            ),
        ):
            prior = row[actual_key]
            if prior is not None:
                if supplied is not None and supplied != prior:
                    raise SwarmConflictError(
                        "Invocation was already reconciled with different usage"
                    )
                continue
            if supplied is None:
                continue
            team[reserved_key] = str(int(team[reserved_key]) - int(row[reservation_key]))
            team[used_key] = decimal_counter(int(team[used_key]) + int(supplied))
            connection.execute(
                f"UPDATE reservations SET {actual_key}=? WHERE id=?",  # noqa: S608 - fixed internal columns
                (supplied, reservation_id),
            )
            overrun |= int(supplied) > int(row[reservation_key])
        self._save_team(connection, team)
        updated = connection.execute(
            "SELECT * FROM reservations WHERE id=?", (reservation_id,)
        ).fetchone()
        complete = (
            updated["actual_tokens"] is not None and updated["actual_cost_microusd"] is not None
        )
        connection.execute(
            "UPDATE reservations SET state=?,closed_at=? WHERE id=?",
            (
                "closed" if complete else "unknown",
                self.clock() if complete else None,
                reservation_id,
            ),
        )
        if overrun:
            self._event(
                connection,
                "budget.overrun",
                "Provider usage exceeded reserved exposure",
                data={"reservation_id": reservation_id},
            )
            if team["state"] == "running":
                self._transition(
                    connection, "blocked", None, "Provider usage exceeded its reservation"
                )
        return dict(
            connection.execute(
                "SELECT * FROM reservations WHERE id=?", (reservation_id,)
            ).fetchone()
        )

    def finish(
        self,
        actor: SwarmActor,
        result: str,
        evidence: list[str],
        verification: dict[str, Any],
        *,
        controller: SwarmController,
    ) -> dict[str, Any]:
        if (
            len(result.encode("utf-8")) > 32_768
            or len(evidence) > 32
            or any(len(item) > 200 for item in evidence)
        ):
            raise ValueError(
                "Result must fit 32 KiB and include at most 32 bounded evidence references"
            )
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            task = self._task(connection, actor.task_id)
            # Accepted replay still requires current membership; a completed attempt
            # is the one intentional exception to the running-attempt check.
            if (
                task["state"] == "succeeded"
                and task["fence"] == actor.task_fence
                and task["owner_id"] == actor.agent_id
            ):
                member = connection.execute(
                    "SELECT * FROM agents WHERE id=?", (actor.agent_id,)
                ).fetchone()
                if (
                    actor.team_id != self.team_id
                    or member is None
                    or not member["active"]
                    or not hmac.compare_digest(member["token_hash"], _actor_digest(actor))
                ):
                    raise SwarmAccessError("Accepted replay requires active membership")
                if task["result"] != result or task["evidence"] != evidence:
                    raise SwarmConflictError("Accepted result is immutable")
                return task
            self._actor(connection, actor, writing=True)
            accepted = verification.get("accepted") is True
            if accepted and actor.task_id == "__swarm_delivery":
                from .autonomy import validate_delivery

                validate_delivery(self, connection)
            verifier_id = verification.get("verifier_id", "")
            quality = verification.get("quality", 1.0)
            if (
                not verifier_id
                or verifier_id == actor.agent_id
                or verification.get("contract_hash") != task_contract_hash(task)
                or verification.get("kind") not in {"review", "javascript"}
                or isinstance(quality, bool)
                or not isinstance(quality, (int, float))
                or not 0 < quality <= 1
                or len(_json(verification).encode("utf-8")) > 16_384
            ):
                raise SwarmAccessError("Trusted verification must bind the original task contract")
            if accepted and (not evidence or not result.strip()):
                raise SwarmConflictError(
                    "Acceptance requires a useful result and objective evidence"
                )
            own_evidence = False
            ancestors = _ancestor_ids(connection, task["id"])
            for reference in evidence:
                row = connection.execute(
                    "SELECT record FROM artifacts WHERE id=?", (reference,)
                ).fetchone()
                if row is None:
                    raise SwarmAccessError("Acceptance evidence must exist inside this team")
                artifact = json.loads(row[0])
                if (
                    artifact["task_id"] == actor.task_id
                    and artifact["attempt_fence"] == actor.task_fence
                ):
                    own_evidence = True
                elif artifact["task_id"] in ancestors:
                    source = self._task(connection, artifact["task_id"])
                    if source["state"] != "succeeded" or reference not in source["evidence"]:
                        raise SwarmAccessError(
                            "Reused dependency evidence must have trusted acceptance"
                        )
                else:
                    raise SwarmAccessError(
                        "Acceptance evidence must bind this attempt or an accepted dependency"
                    )
            if accepted and not own_evidence:
                raise SwarmAccessError("Acceptance requires direct evidence from this task attempt")
            verification_id = _id()
            record = dict(
                verification,
                id=verification_id,
                team_id=self.team_id,
                task_id=actor.task_id,
                agent_id=actor.agent_id,
                fence=actor.task_fence,
                evidence=evidence,
                created_at=self.clock(),
            )
            connection.execute(
                "INSERT INTO verifications VALUES (?,?,?,?,?)",
                (verification_id, actor.task_id, actor.task_fence, int(accepted), _json(record)),
            )
            self._rating(
                connection,
                actor.agent_id,
                task,
                verification_id,
                accepted=accepted,
                quality=float(quality),
                reason=verification.get("reason", "accepted" if accepted else "rejected"),
            )
            if not accepted:
                self._fail_task(
                    connection,
                    task,
                    verification.get("reason", "Verification rejected the result"),
                    retry=True,
                )
                return task
            task.update(state="succeeded", result=result, evidence=evidence, reason="")
            self._save_task(connection, task)
            connection.execute(
                "UPDATE attempts SET state='succeeded',finished_at=? WHERE task_id=? AND fence=?",
                (self.clock(), actor.task_id, actor.task_fence),
            )
            self._idle_agent(connection, actor.agent_id)
            self._event(
                connection,
                "task.succeeded",
                task["title"],
                agent_id=actor.agent_id,
                task_id=actor.task_id,
                data={"verification_id": verification_id},
            )
            return task

    def _rating(
        self,
        connection: sqlite3.Connection,
        agent_id: str,
        task: dict[str, Any],
        evidence_key: str,
        *,
        accepted: bool,
        quality: float = 1.0,
        reason: str = "accepted",
    ) -> dict[str, Any]:
        existing = connection.execute(
            "SELECT record FROM ratings WHERE evidence_key=?", (evidence_key,)
        ).fetchone()
        if existing:
            return json.loads(existing[0])
        row = connection.execute(
            "SELECT record FROM reputation WHERE agent_id=? AND domain=?",
            (agent_id, task["domain"]),
        ).fetchone()
        previous = json.loads(row[0]) if row else initial_profile()
        profile, delta = observe(
            previous,
            difficulty=task["difficulty"],
            accepted=accepted,
            evidence_quality=quality,
            reason=reason,
        )
        connection.execute(
            "INSERT INTO reputation VALUES (?,?,?) ON CONFLICT(agent_id,domain) DO UPDATE SET "
            "record=excluded.record",
            (agent_id, task["domain"], _json(profile)),
        )
        event = dict(
            id=_id(),
            team_id=self.team_id,
            agent_id=agent_id,
            task_id=task["id"],
            evidence_key=evidence_key,
            accepted=accepted,
            reason=reason[:2000],
            difficulty=task["difficulty"],
            domain=task["domain"],
            credit_delta=delta,
            previous_level=previous["level"],
            level=profile["level"],
            reliability=profile["reliability"],
            uncertainty=profile["uncertainty"],
            created_at=self.clock(),
        )
        connection.execute(
            "INSERT INTO ratings VALUES (?,?,?,?,?)",
            (event["id"], agent_id, task["id"], evidence_key, _json(event)),
        )
        agent = json.loads(
            connection.execute("SELECT record FROM agents WHERE id=?", (agent_id,)).fetchone()[0]
        )
        profiles = [
            json.loads(item[0])
            for item in connection.execute(
                "SELECT record FROM reputation WHERE agent_id=?", (agent_id,)
            )
        ]
        total_alpha = 2 + sum(item["alpha"] - 2 for item in profiles)
        total_beta = 2 + sum(item["beta"] - 2 for item in profiles)
        agent.update(
            level=max(item["level"] for item in profiles),
            reliability=total_alpha / (total_alpha + total_beta),
            verified_tasks=str(sum(int(item["verified_tasks"]) for item in profiles)),
        )
        self._save_agent(connection, agent)
        self._event(
            connection,
            "reputation.updated",
            "Verified evidence updated reputation",
            agent_id=agent_id,
            task_id=task["id"],
            data={"rating_id": event["id"], "level": agent["level"]},
        )
        return event

    def record_negative_evidence(
        self,
        controller: SwarmController,
        task_id: str,
        evidence_key: str,
        reason: str,
    ) -> dict[str, Any]:
        if (
            reason not in {"regression", "fabrication", "unverifiable"}
            or not evidence_key
            or len(evidence_key) > 100
        ):
            raise ValueError(
                "A bounded distinct evidence identity and negative category are required"
            )
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            task = self._task(connection, task_id)
            if not task["owner_id"]:
                raise SwarmConflictError("No attributable contributor for this task")
            return self._rating(
                connection, task["owner_id"], task, evidence_key, accepted=False, reason=reason
            )

    def skill_profiles(self, agent_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Owner inspection of bounded domain profiles and attributable rating history."""
        from .skill_profiles import read_profiles

        _page(limit, offset)
        return read_profiles(self, agent_id, limit, offset)

    def set_execution_state(self, actor: SwarmActor, state: str, reason: str = "") -> None:
        """Report provider work/waiting without changing the owned task or its authority."""
        if state not in {"waiting", "running"}:
            raise ValueError("Execution projection state must be waiting or running")
        with self._tx(write=True) as connection:
            self._actor(connection, actor, writing=True)
            if not actor.task_id:
                raise SwarmAccessError("An active task attempt is required")
            row = connection.execute(
                "SELECT record FROM agents WHERE id=?", (actor.agent_id,)
            ).fetchone()
            agent = json.loads(row[0])
            if agent["state"] == state:
                return
            agent["state"] = state
            self._save_agent(connection, agent)
            self._event(
                connection,
                "agent.execution",
                reason[:300] or state,
                agent_id=actor.agent_id,
                task_id=actor.task_id,
                data={"state": state},
            )

    def set_tool_activity(self, actor: SwarmActor, name: str | None) -> None:
        with self._tx(write=True) as connection:
            row = self._actor(connection, actor)
            agent = json.loads(row["record"])
            agent["tool_activity"] = name[:100] if name else None
            self._save_agent(connection, agent)
            self._event(
                connection,
                "agent.tool",
                "Tool activity updated",
                agent_id=actor.agent_id,
                task_id=actor.task_id or None,
                data={"tool": agent["tool_activity"]},
            )

    def register_tool(
        self,
        controller: SwarmController,
        actor: SwarmActor,
        name: str,
        schema: dict[str, Any],
        script: str,
        test_suite: list[dict[str, Any]],
        evidence_id: str,
        request_key: str,
    ) -> dict[str, Any]:
        """Persist a host-verified immutable team tool; no global skill promotion."""
        if (
            not name
            or len(name) > 80
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in name)
        ):
            raise ValueError("Tool name must contain lowercase letters, numbers or underscores")
        if len(script.encode("utf-8")) > 100_000 or len(_json(schema).encode("utf-8")) > 16_384:
            raise ValueError("Tool code/schema exceeds its bounded registry size")
        if (
            not test_suite
            or len(test_suite) > 50
            or len(_json(test_suite).encode("utf-8")) > 32_768
        ):
            raise ValueError("Tool registration requires a bounded host-verified test suite")
        if not request_key or len(request_key) > 100:
            raise ValueError("A bounded registration operation key is required")
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            self._actor(connection, actor, writing=True)
            policy = self._team(connection)["policy"]["tools"]
            if "register_team_tool" not in policy or "run_javascript" not in policy:
                raise SwarmAccessError("Team capability policy does not permit team tools")
            if name in policy:
                raise SwarmConflictError("A team tool cannot shadow a built-in capability")
            evidence = connection.execute(
                "SELECT record FROM artifacts WHERE id=?", (evidence_id,)
            ).fetchone()
            if evidence is None or any(
                json.loads(evidence[0])[key] != value
                for key, value in {
                    "task_id": actor.task_id,
                    "attempt_fence": actor.task_fence,
                    "owner_id": actor.agent_id,
                }.items()
            ):
                raise SwarmAccessError("Tool test evidence must belong to this task attempt")
            digest = _digest(script)
            previous = connection.execute(
                "SELECT record FROM team_tools WHERE name=?", (name,)
            ).fetchone()
            if previous:
                record = json.loads(previous[0])
                if (
                    record["sha256"] != digest
                    or record["request_key"] != request_key
                    or record["schema"] != schema
                    or record["test_suite"] != test_suite
                    or record["evidence_id"] != evidence_id
                    or record["owner_id"] != actor.agent_id
                ):
                    raise SwarmConflictError("Team tool name/version is immutable")
                return record
            record = dict(
                id=_id(),
                team_id=self.team_id,
                name=name,
                owner_id=actor.agent_id,
                task_id=actor.task_id,
                schema=schema,
                script=script,
                test_suite=test_suite,
                evidence_id=evidence_id,
                sha256=digest,
                version=1,
                state="validated",
                request_key=request_key,
                created_at=self.clock(),
                scope="team",
            )
            connection.execute(
                "INSERT INTO team_tools VALUES (?,?,?,?,?)",
                (record["id"], name, actor.agent_id, "validated", _json(record)),
            )
            self._event(
                connection,
                "tool.registered",
                "Verified team tool registered",
                agent_id=actor.agent_id,
                task_id=actor.task_id,
                data={"name": name, "sha256": digest, "evidence_id": evidence_id},
            )
            return record

    def tool_catalog(self, actor: SwarmActor) -> list[dict[str, Any]]:
        with self._tx() as connection:
            self._actor(connection, actor)
            return self._tool_catalog(connection)

    def tool_context(self, actor: SwarmActor) -> dict[str, Any]:
        """Read the current actor, policy and validated tools in one scoped snapshot."""
        with self._tx() as connection:
            member = self._actor(connection, actor)
            return {
                "tools": self._team(connection)["policy"]["tools"],
                "role": member["role"],
                "registered": self._tool_catalog(connection),
            }

    def _tool_catalog(self, connection: Any) -> list[dict[str, Any]]:
        if "run_javascript" not in self._team(connection)["policy"]["tools"]:
            return []
        records = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT record FROM team_tools WHERE state='validated' ORDER BY name LIMIT 50"
            )
        ]
        return [
            {key: record[key] for key in ("id", "team_id", "name", "schema", "sha256", "version")}
            for record in records
        ]

    def get_tool(self, actor: SwarmActor, name: str) -> dict[str, Any]:
        with self._tx() as connection:
            self._actor(connection, actor)
            if "run_javascript" not in self._team(connection)["policy"]["tools"]:
                raise SwarmAccessError("Team JavaScript execution is not permitted")
            row = connection.execute(
                "SELECT record FROM team_tools WHERE name=? AND state='validated'", (name,)
            ).fetchone()
            if row is None:
                raise SwarmAccessError("Validated tool unavailable in this team")
            record = json.loads(row[0])
            if _digest(record["script"]) != record["sha256"]:
                raise SwarmStoreError("Team tool code failed integrity verification")
            return record

    def _event(
        self,
        connection: sqlite3.Connection,
        kind: str,
        summary: str,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = data or {}
        if len(_json(data).encode("utf-8")) > 4096:
            raise ValueError("Event data exceeds 4 KiB")
        event = dict(
            id=_id(),
            team_id=self.team_id,
            kind=kind[:100],
            summary=summary[:500],
            agent_id=agent_id,
            task_id=task_id,
            trace_id=_id(),
            created_at=self.clock(),
            data=data,
        )
        cursor = connection.execute(
            "INSERT INTO events (id,kind,agent_id,task_id,trace_id,created_at,record) VALUES "
            "(?,?,?,?,?,?,?)",
            (
                event["id"],
                event["kind"],
                agent_id,
                task_id,
                event["trace_id"],
                event["created_at"],
                _json(event),
            ),
        )
        event["seq"] = str(cursor.lastrowid)
        return event

    def append_event(
        self,
        scope: SwarmActor | SwarmController,
        kind: str,
        summary: str,
        task_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._tx(write=True) as connection:
            agent_id = None
            if isinstance(scope, SwarmController):
                self._controller(connection, scope)
            else:
                self._actor(connection, scope, writing=True)
                if not kind.startswith("worker."):
                    raise SwarmAccessError("Workers may only append untrusted worker events")
                agent_id, task_id = scope.agent_id, scope.task_id or task_id
            if task_id:
                self._task(connection, task_id)
            return self._event(
                connection, kind, summary, agent_id=agent_id, task_id=task_id, data=data
            )

    def records(self, kind: str, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        limit, offset = _page(limit, offset)
        tables = {
            "tasks": "tasks",
            "agents": "agents",
            "messages": "messages",
            "artifacts": "artifacts",
            "reputation": "ratings",
            "publications": "publications",
            "tools": "team_tools",
            "verifications": "verifications",
            "decisions": "decisions",
        }
        with self._tx() as connection:
            self._team(connection)
            if kind == "checkpoints":
                return [
                    json.loads(row[0])
                    for row in connection.execute(
                        "SELECT record FROM decisions WHERE json_extract(record,'$.kind')="
                        "'checkpoint_snapshot' ORDER BY rowid DESC LIMIT ? OFFSET ?",
                        (min(limit, 32), offset),
                    )
                ]
            if kind == "events":
                return [
                    dict(json.loads(row["record"]), seq=str(row["seq"]))
                    for row in connection.execute(
                        "SELECT seq,record FROM events ORDER BY seq DESC LIMIT ? OFFSET ?",
                        (limit, offset),
                    )
                ]
            if kind == "reservations":
                return [
                    dict(row)
                    for row in connection.execute(
                        "SELECT * FROM reservations ORDER BY created_at,id LIMIT ? OFFSET ?",
                        (limit, offset),
                    )
                ]
            if kind not in tables:
                raise ValueError("Unknown team record collection")
            # Table identifiers come exclusively from this fixed internal mapping.
            return [
                json.loads(row[0])
                for row in connection.execute(
                    f"SELECT record FROM {tables[kind]} ORDER BY rowid LIMIT ? OFFSET ?",  # noqa: S608
                    (limit, offset),
                )
            ]  # noqa: S608

    def get_record(self, kind: str, record_id: str) -> dict[str, Any]:
        """Indexed owner inspector lookup, independently of collection pagination."""
        tables = {
            "tasks": "tasks",
            "agents": "agents",
            "decisions": "decisions",
            "checkpoints": "decisions",
        }
        if kind not in tables:
            raise ValueError("Select a task, agent, decision or checkpoint inspector record")
        with self._tx() as connection:
            self._team(connection)
            row = connection.execute(
                f"SELECT record FROM {tables[kind]} WHERE id=?",  # noqa: S608
                (record_id,),
            ).fetchone()
            if row is None:
                raise SwarmAccessError("Record unavailable in this team")
            record = json.loads(row[0])
            if kind == "checkpoints" and record.get("kind") != "checkpoint_snapshot":
                raise SwarmAccessError("Checkpoint snapshot unavailable in this team")
            return record

    def events_after(
        self, after: str | int = "0", *, limit: int = 100, actor: SwarmActor | None = None
    ) -> list[dict[str, Any]]:
        _page(limit)
        sequence = int(decimal_counter(after))
        with self._tx() as connection:
            if actor is not None:
                self._actor(connection, actor)
            else:
                self._team(connection)
            if sequence > 2**63 - 1:
                return []
            return [
                dict(json.loads(row["record"]), seq=str(row["seq"]))
                for row in connection.execute(
                    "SELECT seq,record FROM events WHERE seq>? ORDER BY seq LIMIT ?",
                    (sequence, limit),
                )
            ]

    def world(self, group: str = "") -> dict[str, Any]:
        with self._tx() as connection:
            team = self._team(connection)
            count = connection.execute("SELECT count(*) FROM agents").fetchone()[0]
            task_counts = {
                row[0]: str(row[1])
                for row in connection.execute("SELECT state,count(*) FROM tasks GROUP BY state")
            }
            groups = []
            agents = []
            if count <= 100 or group:
                agents = [
                    json.loads(row[0])
                    for row in connection.execute(
                        "SELECT record FROM agents WHERE (?='' OR "
                        "json_extract(record,'$.group_id')=?) ORDER BY id LIMIT 100",
                        (group, group),
                    )
                ]
            else:
                for row in connection.execute(
                    "SELECT json_extract(record,'$.group_id') AS group_id,count(*) AS "
                    "n,max(json_extract(record,'$.level')) AS level FROM agents GROUP BY "
                    "group_id ORDER BY n DESC,group_id LIMIT 50"
                ):
                    groups.append(
                        dict(
                            id=row["group_id"],
                            title=row["group_id"],
                            agents=str(row["n"]),
                            level=row["level"],
                            counts={"agents": str(row["n"])},
                        )
                    )
            tasks = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT record FROM tasks WHERE (?='' OR "
                    "json_extract(record,'$.milestone')=?) ORDER BY state='running' "
                    "DESC,state='ready' DESC,rowid DESC LIMIT 20",
                    (group, group),
                )
            ]
            # The detailed inspector retrieves full task bodies separately.
            for task in tasks:
                for key in ("description", "acceptance", "result", "verification_script"):
                    task[key] = task[key][:200]
                task["evidence"] = task["evidence"][:3]
                task["dependencies"] = task["dependencies"][:10]
            events = [
                dict(json.loads(row["record"]), seq=str(row["seq"]))
                for row in connection.execute(
                    "SELECT seq,record FROM events ORDER BY seq DESC LIMIT 12"
                )
            ]
            revision = str(
                connection.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
            )
            projected_team = dict(
                team,
                goal=team["goal"][:1000],
                acceptance=team.get("acceptance", "")[:1000],
                checkpoint={},
            )
            snapshot: dict[str, Any] = dict(
                team=projected_team,
                revision=revision,
                agents=agents,
                tasks=tasks,
                groups=groups,
                activity=events,
                counts=dict(
                    task_counts,
                    agents=str(count),
                    tasks=str(sum(int(value) for value in task_counts.values())),
                    artifacts=str(
                        connection.execute("SELECT count(*) FROM artifacts").fetchone()[0]
                    ),
                    publications=str(
                        connection.execute(
                            "SELECT count(*) FROM publications WHERE state='published'"
                        ).fetchone()[0]
                    ),
                ),
                aggregated=count > 100 and not group,
                has_more=count > len(agents) if agents else bool(groups),
            )
            # Reserve 4 KiB for the WebSocket envelope, including escaped Unicode.
            while len(json.dumps(snapshot, ensure_ascii=True).encode("utf-8")) > 60_000:
                for key in ("activity", "tasks", "agents", "groups"):
                    if snapshot[key]:
                        snapshot[key].pop()
                        snapshot["has_more"] = True
                        break
                else:
                    snapshot["team"] = dict(
                        projected_team,
                        policy=dict(
                            team["policy"], tools=[], allowed_domains=[], dependency_registries=[]
                        ),
                    )
                    snapshot["has_more"] = True
                    break
            return snapshot
