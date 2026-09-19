"""Owner-scoped portable backups, recoverable replacement and explicit retention.

Filesystem transitions use a durable journal. A validated replacement rotates
execution credentials before publication, quarantines the old namespace, and
can be retried after any interrupted move without trusting an uploaded path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import zipfile
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from filelock import FileLock, Timeout

from .store import (
    SwarmAccessError,
    SwarmConflictError,
    SwarmStoreError,
    TeamRegistry,
    TeamStore,
    _connection,
    _digest,
    _json,
)
from .streams import CHUNK_BYTES

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 20_000
_ID = re.compile(r"[a-f0-9]{32}")
_HASH = re.compile(r"[a-f0-9]{64}")
log = logging.getLogger(__name__)
_PUBLICATION_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS publications (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
    "team_id TEXT NOT NULL, artifact_id TEXT NOT NULL, destination TEXT NOT NULL, "
    "destination_version INTEGER NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL)"
)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _path(root: Path, *parts: str) -> Path:
    result = root.joinpath(*parts)
    if result.is_symlink() or not result.resolve().is_relative_to(root.resolve()):
        raise SwarmAccessError("Storage path escaped its namespace")
    current = result
    while current != root:
        if current.is_symlink():
            raise SwarmAccessError("Storage links are not supported")
        current = current.parent
    return result


def _move(source: Path, target: Path) -> None:
    """Windows readers release incrementally; a failed move stays retryable."""
    target.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 4
    while True:
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise SwarmStoreError(
                    "Storage replacement is pending; close readers and retry"
                ) from exc
            time.sleep(0.02 + secrets.randbelow(60) / 1000)


def _safe_sqlite(path: Path, *, publications: bool = False) -> None:
    """Uploaded databases may contain data, never executable views or triggers."""
    with closing(sqlite3.connect(":memory:")) as expected:
        expected.executescript(
            _PUBLICATION_SCHEMA
            if publications
            else Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        )
        definitions = {
            (kind, name): " ".join(sql.split()) if sql else None
            for kind, name, sql in expected.execute("SELECT type,name,sql FROM sqlite_schema")
        }
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        source.execute("PRAGMA trusted_schema=OFF")
        steps = 0

        def bounded() -> int:
            nonlocal steps
            steps += 1
            return int(steps > 50_000)

        source.set_progress_handler(bounded, 10_000)
        for kind, name, sql in source.execute("SELECT type,name,sql FROM sqlite_schema"):
            if (kind, name) not in definitions or definitions[(kind, name)] != (
                " ".join(sql.split()) if sql else None
            ):
                raise SwarmStoreError("Backup database contains an unsupported schema object")
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise SwarmStoreError("Backup database failed integrity verification")


def _published_records(snapshot: Path, mode: str) -> list[dict[str, Any]]:
    if mode == "local":
        with closing(sqlite3.connect(snapshot / "team.sqlite3")) as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT record FROM publications WHERE state='published'"
                )
            ]
    records = []
    with (snapshot / "publications.jsonl").open(encoding="utf-8") as stream:
        columns = json.loads(stream.readline())["columns"]
        for line in stream:
            row = dict(zip(columns, json.loads(line), strict=True))
            if row["state"] == "published":
                records.append(row["record"])
                if len(records) > MAX_MEMBERS:
                    raise SwarmStoreError("Too many publications for one portable backup")
    return records


def _verify_publications(path: Path, team_id: str, expected: list[dict[str, Any]]) -> None:
    _safe_sqlite(path, publications=True)
    indexed = {row["id"]: row for row in expected}
    if len(indexed) > MAX_MEMBERS:
        raise SwarmStoreError("Too many publications for one portable backup")
    seen = set()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        for row in connection.execute(
            "SELECT "
            "rowid,id,team_id,artifact_id,destination,destination_version,sha256,length(content) "
            "FROM publications"
        ):
            rowid, publication_id, scope, artifact_id, destination, version, digest, size = row
            reference = indexed.get(publication_id)
            if (
                reference is None
                or scope != team_id
                or (artifact_id, destination, version, digest)
                != (
                    reference["artifact_id"],
                    reference["destination_id"],
                    reference["destination_version"],
                    reference["sha256"],
                )
                or size > 50_000_000
            ):
                raise SwarmStoreError("Published bytes do not match the selected team's provenance")
            calculated = hashlib.sha256()
            with connection.blobopen("publications", "content", rowid, readonly=True) as content:
                for chunk in iter(lambda: content.read(CHUNK_BYTES), b""):
                    calculated.update(chunk)
            if calculated.hexdigest() != digest:
                raise SwarmStoreError("Published content failed hash verification")
            seen.add(publication_id)
    if seen != indexed.keys():
        raise SwarmStoreError("Backup omits selected publication content")


class StorageLifecycle:
    def __init__(self, root: Path, registry: Any, *, owner: str = "local-user"):
        self.root = root.resolve()
        self.registry = registry
        self.owner = owner
        self.directory = self.root / "lifecycle"
        self.journal = self.directory / "operations.sqlite3"
        self.lock = threading.RLock()

    @contextmanager
    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.lock:
            try:
                with FileLock(self.directory / "operations.lock", timeout=30):
                    yield
            except Timeout as exc:
                raise SwarmConflictError(
                    "Another storage operation is active; retry shortly"
                ) from exc

    def _journal(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.journal, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
            "request_key TEXT NOT NULL, kind TEXT NOT NULL, team_id TEXT NOT NULL, fingerprint "
            "TEXT NOT NULL, record TEXT NOT NULL, UNIQUE(owner,kind,request_key))"
        )
        return connection

    def _operation(
        self, kind: str, request_key: str, team_id: str, fingerprint: str
    ) -> dict[str, Any]:
        if not request_key or len(request_key) > 100:
            raise ValueError("A bounded idempotency key is required")
        with closing(self._journal()) as connection, connection:
            row = connection.execute(
                "SELECT record,fingerprint,team_id FROM operations WHERE owner=? AND kind=? AND "
                "request_key=?",
                (self.owner, kind, request_key),
            ).fetchone()
            if row:
                if row["fingerprint"] != fingerprint or row["team_id"] != team_id:
                    raise SwarmConflictError("Storage operation key identifies different content")
                return json.loads(row["record"])
            record = {
                "id": _digest(_json([self.owner, kind, request_key, fingerprint]))[:32],
                "team_id": team_id,
                "kind": kind,
                "phase": "new",
                "created_at": time.time(),
            }
            connection.execute(
                "INSERT INTO operations VALUES (?,?,?,?,?,?,?)",
                (record["id"], self.owner, request_key, kind, team_id, fingerprint, _json(record)),
            )
            return record

    def _save(self, operation: dict[str, Any]) -> None:
        with closing(self._journal()) as connection, connection:
            connection.execute(
                "UPDATE operations SET record=? WHERE id=? AND owner=?",
                (_json(operation), operation["id"], self.owner),
            )

    def _remote_fingerprint(self) -> str:
        if self.registry.remote is None:
            raise SwarmStoreError("Reconnect the selected distributed storage before continuing")
        return _digest(_json(self.registry.remote.config.model_dump(mode="json")))

    def _check_backend(self, operation: dict[str, Any]) -> None:
        if (
            operation.get("mode") == "distributed"
            and operation.get("storage_fingerprint") != self._remote_fingerprint()
        ):
            raise SwarmConflictError(
                "Storage settings changed; reconnect the original destination "
                "or start a new restore"
            )

    def _assert_owned(self, team_id: str) -> None:
        TeamRegistry._validate_identity(team_id)
        try:
            self.registry.local._assert_visible(team_id, self.owner)
            return
        except SwarmAccessError:
            if self.journal.is_file():
                with closing(self._journal()) as connection:
                    if connection.execute(
                        "SELECT 1 FROM operations WHERE owner=? AND team_id=? LIMIT 1",
                        (self.owner, team_id),
                    ).fetchone():
                        return
            if self.registry.remote is None:
                raise
        with self.registry.remote.database.transaction() as connection:
            if not connection.execute(
                "SELECT 1 FROM teams WHERE id=? AND owner=?", (team_id, self.owner)
            ).fetchone():
                raise SwarmAccessError("Team unavailable for this owner")

    def status(self, team_id: str) -> dict[str, Any]:
        self._assert_owned(team_id)
        if not self.journal.is_file():
            return {"backups": []}
        with closing(self._journal()) as connection:
            rows = connection.execute(
                "SELECT record FROM operations WHERE owner=? AND team_id=? AND kind='backup' "
                "ORDER BY rowid DESC LIMIT 50",
                (self.owner, team_id),
            ).fetchall()
        backups = []
        for row in rows:
            operation = json.loads(row[0])
            if (
                operation["phase"] == "done"
                and _path(self.directory, "exports", operation["id"] + ".zip").is_file()
            ):
                backups.append(self._backup_result(operation))
        with closing(self._journal()) as connection:
            pending = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT record FROM operations WHERE owner=? AND team_id=? AND kind='restore' "
                    "AND json_extract(record,'$.phase')='validated' ORDER BY rowid DESC LIMIT 20",
                    (self.owner, team_id),
                )
            ]
        return {
            "backups": backups,
            "pending_restores": pending,
            "max_archive_bytes": str(MAX_ARCHIVE_BYTES),
        }

    def restore_operation(self, restore_id: str) -> dict[str, Any]:
        if not _ID.fullmatch(restore_id) or not self.journal.is_file():
            raise SwarmAccessError("Restore operation unavailable")
        with closing(self._journal()) as connection:
            row = connection.execute(
                "SELECT record FROM operations WHERE id=? AND owner=? AND kind='restore'",
                (restore_id, self.owner),
            ).fetchone()
        if row is None or json.loads(row[0])["phase"] == "new":
            raise SwarmAccessError("Restore is not ready to resume")
        return json.loads(row[0])

    def pending_restores(self) -> dict[str, Any]:
        if not self.journal.is_file():
            return {"backups": [], "pending_restores": []}
        with closing(self._journal()) as connection:
            operations = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT record FROM operations WHERE owner=? AND kind='restore' "
                    "AND json_extract(record,'$.phase')='validated' ORDER BY rowid DESC LIMIT 20",
                    (self.owner,),
                )
            ]
        return {"backups": [], "pending_restores": operations}

    @staticmethod
    def _backup_result(operation: dict[str, Any]) -> dict[str, Any]:
        return {
            "backup_id": operation["id"],
            "team_id": operation["team_id"],
            "created_at": operation["created_at"],
            "size_bytes": operation.get("size_bytes", "0"),
            "sha256": operation.get("sha256", ""),
            "artifacts": operation.get("artifacts", "0"),
            "download_url": f"/api/swarm/teams/{operation['team_id']}/backups/{operation['id']}",
        }

    def backup(
        self, team_id: str, request_key: str, expected_version: int | None = None
    ) -> dict[str, Any]:
        with self._locked():
            store = self.registry.open(team_id, self.owner)
            team = store.get()
            operation = self._operation(
                "backup", request_key, team_id, _digest(_json([team_id, expected_version]))
            )
            if operation["phase"] == "done":
                return self._backup_result(operation)
            if expected_version is not None and team["version"] != expected_version:
                raise SwarmConflictError("The team changed; refresh before taking a backup")
            target = _path(self.directory, "exports", operation["id"] + ".zip")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="export-", dir=self.directory) as temporary:
                staging = Path(temporary)
                manifest = store.backup(staging / "team")
                expected = _published_records(staging / "team", team["mode"])
                self._export_publications(staging / "publications.sqlite3", team_id, expected)
                entries: dict[str, dict[str, str]] = {}
                total = 0
                for file in staging.rglob("*"):
                    if not file.is_file() or file.name.endswith(("-wal", "-shm")):
                        continue
                    size = file.stat().st_size
                    total += size
                    if total > MAX_ARCHIVE_BYTES - 8_000_000 or len(entries) >= MAX_MEMBERS:
                        raise SwarmStoreError("This team exceeds the 512 MiB portable backup limit")
                    entries[file.relative_to(staging).as_posix()] = {
                        "size": str(size),
                        "sha256": _hash_file(file),
                    }
                envelope = {
                    "format": "personal-jarvis-swarm",
                    "version": 1,
                    "team_id": team_id,
                    "mode": team["mode"],
                    "files": entries,
                }
                (staging / "archive.json").write_text(_json(envelope), encoding="utf-8")
                pending = target.with_suffix(".pending")
                with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_STORED) as archive:
                    for name in [*entries, "archive.json"]:
                        archive.write(staging / name, name)
                os.replace(pending, target)
                operation.update(
                    phase="done",
                    size_bytes=str(target.stat().st_size),
                    sha256=_hash_file(target),
                    artifacts=str(manifest["artifacts"]),
                )
                self._save(operation)
            return self._backup_result(operation)

    def _export_publications(
        self, target: Path, team_id: str, expected: list[dict[str, Any]]
    ) -> None:
        with closing(sqlite3.connect(target)) as connection, connection:
            connection.execute(_PUBLICATION_SCHEMA)
            source = self.root / "publications.sqlite3"
            if expected and source.is_file():
                connection.execute("ATTACH DATABASE ? AS source", (str(source),))
                for record in expected:
                    connection.execute(
                        "INSERT INTO publications SELECT * FROM source.publications WHERE id=? "
                        "AND team_id=? AND owner=?",
                        (record["id"], team_id, self.owner),
                    )
        _verify_publications(target, team_id, expected)

    def backup_file(self, team_id: str, backup_id: str) -> Path:
        self._assert_owned(team_id)
        if not _ID.fullmatch(backup_id) or not self.journal.is_file():
            raise SwarmAccessError("Backup unavailable")
        with closing(self._journal()) as connection:
            row = connection.execute(
                "SELECT record FROM operations WHERE id=? AND owner=? AND team_id=? AND "
                "kind='backup'",
                (backup_id, self.owner, team_id),
            ).fetchone()
        if row is None or json.loads(row[0])["phase"] != "done":
            raise SwarmAccessError("Backup unavailable")
        path = _path(self.directory, "exports", backup_id + ".zip")
        if not path.is_file():
            raise SwarmStoreError("Backup has expired; create another export")
        return path

    def prepare_restore(
        self, upload: Path, request_key: str, replace_team_id: str = ""
    ) -> dict[str, Any]:
        """Validate the entire upload and stage safe data before touching a live team."""
        if upload.stat().st_size > MAX_ARCHIVE_BYTES:
            raise SwarmStoreError("Backup exceeds the 512 MiB upload limit")
        with self._locked(), zipfile.ZipFile(upload) as archive:
            infos = archive.infolist()
            names = [entry.filename for entry in infos]
            if (
                len(infos) > MAX_MEMBERS
                or len(set(names)) != len(names)
                or "archive.json" not in names
            ):
                raise SwarmStoreError("Backup has duplicate, missing or excessive entries")
            expanded = 0
            for entry in infos:
                path = PurePosixPath(entry.filename)
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if (
                    entry.filename != path.as_posix()
                    or path.is_absolute()
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or "\\" in entry.filename
                    or ":" in entry.filename
                    or mode not in {0, stat.S_IFREG}
                    or entry.flag_bits & 1
                    or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or entry.file_size > MAX_ARCHIVE_BYTES
                    or entry.file_size > max(1, entry.compress_size) * 200
                    or (
                        entry.filename != "archive.json"
                        and not re.fullmatch(
                            r"(?:publications\.sqlite3|team/(?:manifest\.json|team\.sqlite3|[a-z_]+\.jsonl|objects/[a-f0-9]{64}))",
                            entry.filename,
                        )
                    )
                ):
                    raise SwarmStoreError(
                        "Backup contains an unsafe path, link or compressed entry"
                    )
                expanded += entry.file_size
            if (
                expanded > MAX_EXPANDED_BYTES
                or archive.getinfo("archive.json").file_size > 4_000_000
            ):
                raise SwarmStoreError("Backup exceeds its expanded data limit")
            envelope = json.loads(archive.read("archive.json"))
            if envelope.get("format") != "personal-jarvis-swarm" or envelope.get("version") != 1:
                raise SwarmStoreError("Unsupported Swarm backup format")
            team_id = envelope.get("team_id", "")
            TeamRegistry._validate_identity(team_id)
            storage_mode = envelope.get("mode")
            if storage_mode not in {"local", "distributed"}:
                raise SwarmStoreError("Unsupported Swarm backup storage mode")
            if replace_team_id and replace_team_id != team_id:
                raise SwarmAccessError("Replacement backup belongs to a different team")
            entries = envelope.get("files", {})
            if not isinstance(entries, dict) or set(entries) != set(names) - {"archive.json"}:
                raise SwarmStoreError("Backup manifest and file inventory disagree")
            fingerprint = _digest(_json([_hash_file(upload), replace_team_id]))
            operation = self._operation("restore", request_key, team_id, fingerprint)
            if operation["phase"] != "new":
                return operation
            if storage_mode == "local":
                catalog_path = self.registry.local.root / "catalog.sqlite3"
                if catalog_path.is_file():
                    with _connection(catalog_path) as connection:
                        cleanup = connection.execute(
                            "SELECT owner FROM team_cleanup WHERE id=?", (team_id,)
                        ).fetchone()
                        existing = connection.execute(
                            "SELECT owner FROM teams WHERE id=?", (team_id,)
                        ).fetchone()
                    if cleanup:
                        if cleanup[0] != self.owner:
                            raise SwarmAccessError("Deleted identity belongs to a different owner")
                        raise SwarmConflictError(
                            "This identity was permanently deleted here; "
                            "restore its backup into a separate instance"
                        )
                    if existing and existing[0] != self.owner:
                        raise SwarmAccessError("Existing team belongs to a different owner")
                    if existing and not replace_team_id:
                        raise SwarmConflictError(
                            "This team already exists; explicitly select replacement"
                        )
            staged = _path(self.directory, "staging", operation["id"])
            if staged.exists():
                shutil.rmtree(staged)
            staged.mkdir(parents=True)
            try:
                for entry in infos:
                    if entry.filename == "archive.json":
                        continue
                    expected = entries[entry.filename]
                    if (
                        not isinstance(expected, dict)
                        or not _HASH.fullmatch(str(expected.get("sha256", "")))
                        or str(entry.file_size) != expected.get("size")
                    ):
                        raise SwarmStoreError("Backup entry has invalid size or hash metadata")
                    destination = _path(staged, *PurePosixPath(entry.filename).parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    size = 0
                    with archive.open(entry) as source, destination.open("xb") as output:
                        for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
                            size += len(chunk)
                            if size > entry.file_size:
                                raise SwarmStoreError("Backup expanded beyond its declared length")
                            digest.update(chunk)
                            output.write(chunk)
                    if size != entry.file_size or digest.hexdigest() != expected["sha256"]:
                        raise SwarmStoreError("Backup data failed content verification")
                snapshot = staged / "team"
                _path(snapshot, "objects").mkdir(exist_ok=True)
                manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
                if manifest.get("team_id") != team_id:
                    raise SwarmAccessError("Backup manifests disagree about team ownership")
                if storage_mode == "local":
                    _safe_sqlite(snapshot / "team.sqlite3")
                    temporary_registry = TeamRegistry(staged / "prepared")
                    team = temporary_registry.restore(
                        snapshot, self.owner, request_key="validated-restore"
                    )
                    if team["id"] != team_id:
                        raise SwarmAccessError("Backup team identity does not match its manifest")
                    restored = temporary_registry.open(team_id, self.owner)
                    with restored._tx(write=True) as connection:
                        for row in connection.execute("SELECT id FROM agents").fetchall():
                            connection.execute(
                                "UPDATE agents SET token_hash=? WHERE id=?",
                                (_digest(secrets.token_urlsafe(32)), row[0]),
                            )
                        connection.execute(
                            "UPDATE controller SET token_hash=?,expires_at=0,fence=fence+1",
                            (_digest(secrets.token_urlsafe(32)),),
                        )
                        team = restored._team(connection)
                        team["storage_generation"] = operation["id"]
                        if team["state"] not in {"succeeded", "failed", "canceled", "archived"}:
                            preparation = team.get("checkpoint", {}).get("preparation", {})
                            team.update(
                                state="created"
                                if preparation.get("required")
                                and preparation.get("state") != "launched"
                                else "paused",
                                reason="Restored backup; review before resuming",
                            )
                        restored._save_team(connection, team)
                    (staged / "prepared" / team_id / ".restore-id").write_text(
                        operation["id"], encoding="ascii"
                    )
                else:
                    if self.registry.remote is None:
                        raise SwarmStoreError(
                            "Configure distributed storage before restoring this backup"
                        )
                    from .distributed.snapshot import validate_database_rows

                    validate_database_rows(self.registry.remote, snapshot, owner=self.owner)
                expected_publications = _published_records(snapshot, storage_mode)
                _verify_publications(
                    staged / "publications.sqlite3", team_id, expected_publications
                )
                self._merge_publications(staged / "publications.sqlite3", team_id, check_only=True)
                operation.update(
                    phase="validated", mode=storage_mode, replace=bool(replace_team_id)
                )
                if storage_mode == "distributed":
                    operation["storage_fingerprint"] = self._remote_fingerprint()
                self._save(operation)
                return operation
            except BaseException:
                # This directory contains only this validated operation's upload.
                shutil.rmtree(_path(self.directory, "staging", operation["id"]))
                raise

    def _merge_publications(self, source: Path, team_id: str, *, check_only: bool = False) -> None:
        destination = self.root / "publications.sqlite3"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(destination, timeout=30)) as connection, connection:
            connection.execute(_PUBLICATION_SCHEMA)
            connection.execute("ATTACH DATABASE ? AS incoming", (str(source),))
            for row in connection.execute(
                "SELECT id,artifact_id,destination,destination_version,sha256 FROM "
                "incoming.publications WHERE team_id=?",
                (team_id,),
            ):
                existing = connection.execute(
                    "SELECT owner,team_id,artifact_id,destination,destination_version,sha256 "
                    "FROM main.publications WHERE id=?",
                    (row[0],),
                ).fetchone()
                if existing and existing != (self.owner, team_id, *row[1:]):
                    raise SwarmConflictError(
                        "A publication identity already belongs to different content"
                    )
            if not check_only:
                connection.execute(
                    "INSERT OR IGNORE INTO main.publications SELECT "
                    "id,?,team_id,artifact_id,destination,destination_version,sha256,content "
                    "FROM incoming.publications WHERE team_id=?",
                    (self.owner, team_id),
                )

    def apply_restore(self, operation: dict[str, Any]) -> dict[str, Any]:
        """Complete a validated restore; interrupted file moves are retryable."""
        with self._locked():
            operation = self.restore_operation(operation["id"])
            self._check_backend(operation)
            team_id = operation["team_id"]
            staged = _path(self.directory, "staging", operation["id"])
            if operation["phase"] == "done":
                team = self.registry.open(team_id, self.owner).get()
                return {
                    "team": team,
                    "restore_id": operation["id"],
                    "quarantine_id": operation.get("quarantine_id"),
                }
            if operation["mode"] == "distributed":
                team = self.registry.remote.restore(
                    staged / "team",
                    owner=self.owner,
                    request_key=operation["id"],
                    replace_existing=operation["replace"],
                )
                self._merge_publications(staged / "publications.sqlite3", team_id)
                if operation["replace"]:
                    operation["quarantine_id"] = operation["id"]
                    with self.registry.remote.database.transaction() as connection:
                        row = connection.execute(
                            "SELECT created_at FROM restore_quarantine "
                            "WHERE request_key=? AND owner=?",
                            (operation["id"], self.owner),
                        ).fetchone()
                    if row:
                        operation["quarantined_at"] = row[0]
            else:
                local = self.registry.local
                local.provision()
                target = _path(local.root, team_id)
                prepared = _path(staged, "prepared", team_id)
                quarantine = _path(self.directory, "quarantine", operation["id"], team_id)
                with _connection(local.root / "catalog.sqlite3", write=True) as catalog:
                    existing = catalog.execute(
                        "SELECT owner FROM teams WHERE id=?", (team_id,)
                    ).fetchone()
                    marker = target / ".restore-id"
                    already_moved = (
                        marker.is_file() and marker.read_text(encoding="ascii") == operation["id"]
                    )
                    if existing and existing[0] != self.owner:
                        raise SwarmAccessError("Existing team belongs to a different owner")
                    cleanup = catalog.execute(
                        "SELECT owner,completed_at FROM team_cleanup WHERE id=?", (team_id,)
                    ).fetchone()
                    if cleanup:
                        if cleanup["owner"] != self.owner:
                            raise SwarmAccessError("Deleted identity belongs to a different owner")
                        raise SwarmConflictError(
                            "This identity was deleted here; restore into a separate instance"
                        )
                    if (
                        not operation["replace"]
                        and (existing or target.exists())
                        and not already_moved
                    ):
                        raise SwarmConflictError(
                            "This team already exists; explicitly select replacement"
                        )
                    if (
                        operation["replace"]
                        and not existing
                        and not quarantine.exists()
                        and not already_moved
                    ):
                        raise SwarmAccessError("Replacement requires an existing owned team")
                    # Publication conflicts are resolved before quarantining any bytes.
                    self._merge_publications(staged / "publications.sqlite3", team_id)
                    if not already_moved:
                        if target.exists():
                            if quarantine.exists():
                                raise SwarmConflictError(
                                    "Quarantine already exists; retry the original operation"
                                )
                            _move(target, quarantine)
                        _move(prepared, target)
                    restored = TeamStore(target / "team.sqlite3", team_id)
                    team = restored.get()
                    catalog.execute(
                        "DELETE FROM teams WHERE id=? AND owner=?", (team_id, self.owner)
                    )
                    catalog.execute(
                        "INSERT INTO teams VALUES (?,?,?,?,?,?)",
                        (
                            team_id,
                            self.owner,
                            operation["id"],
                            team["name"],
                            team["created_at"],
                            "restore:" + operation["id"],
                        ),
                    )
                if quarantine.exists():
                    operation["quarantine_id"] = operation["id"]
                    operation["quarantined_at"] = quarantine.parent.stat().st_mtime
            operation["phase"] = "done"
            self._save(operation)
            if staged.exists():
                shutil.rmtree(_path(self.directory, "staging", operation["id"]))
            return {
                "team": team,
                "restore_id": operation["id"],
                "quarantine_id": operation.get("quarantine_id"),
            }

    def restore_installed(self, operation: dict[str, Any]) -> bool:
        """Reconcile the committed namespace before fencing an operation retry."""
        self._check_backend(operation)
        if operation["mode"] == "distributed":
            with self.registry.remote.database.transaction() as connection:
                return bool(
                    connection.execute(
                        "SELECT 1 FROM teams WHERE id=? AND owner=? AND request_key=?",
                        (operation["team_id"], self.owner, operation["id"]),
                    ).fetchone()
                )
        marker = _path(self.registry.local.root, operation["team_id"], ".restore-id")
        return marker.is_file() and marker.read_text(encoding="ascii") == operation["id"]

    def fence_restore(self, operation: dict[str, Any]) -> bool:
        """Check the replacement identity inside the same authority transaction as fencing."""
        team_id = operation["team_id"]
        with self._locked():
            if self.restore_installed(operation):
                return False
            self._assert_owned(team_id)
            try:
                store = self.registry.open(team_id, self.owner)
                with store._tx(write=True) as connection:
                    if operation["mode"] == "distributed":
                        namespace = self.registry.remote.config.namespace
                        installed = connection.raw.execute(
                            f'SELECT request_key FROM "{namespace}".teams WHERE id=%s AND owner=%s',  # noqa: S608
                            (team_id, self.owner),
                        ).fetchone()
                        if installed and installed[0] == operation["id"]:
                            return False
                    elif self.restore_installed(operation):
                        return False
                    team = store._team(connection)
                    if team["state"] not in {"succeeded", "failed", "canceled", "archived"}:
                        store._transition(
                            connection, "canceled", None, "User replaced team storage"
                        )
                        connection.execute("UPDATE controller SET expires_at=0 WHERE singleton=1")
            except SwarmConflictError:
                raise
            except (SwarmStoreError, sqlite3.DatabaseError):
                # Broken data cannot keep live credentials usable. Catalog
                # ownership, checked above, authorizes quarantine of those bytes.
                log.warning("Owned team data needs quarantine before restore", exc_info=True)
            return True

    def prepare_delete(self, team_id: str, request_key: str) -> dict[str, Any]:
        with self._locked():
            self._assert_owned(team_id)
            operation = self._operation("delete", request_key, team_id, team_id)
            local = self.registry.local
            local_owned = False
            if (local.root / "catalog.sqlite3").is_file():
                with _connection(local.root / "catalog.sqlite3") as connection:
                    local_owned = bool(
                        connection.execute(
                            "SELECT 1 FROM teams WHERE id=? AND owner=? UNION ALL "
                            "SELECT 1 FROM team_cleanup WHERE id=? AND owner=?",
                            (team_id, self.owner, team_id, self.owner),
                        ).fetchone()
                    )
            if not local_owned and self.registry.remote is not None:
                if (
                    self.registry.remote.deletion_result(team_id, self.owner, operation["id"])
                    is not None
                ):
                    operation["phase"] = "done"
                    self._save(operation)
            return operation

    def delete(
        self, team_id: str, request_key: str, expected_storage_generation: str | None = None
    ) -> dict[str, Any]:
        with self._locked():
            self._assert_owned(team_id)
            operation = self._operation("delete", request_key, team_id, team_id)
            if operation["phase"] == "done":
                return {"team_id": team_id, "deleted": True}
            local = self.registry.local
            local_owned = False
            if (local.root / "catalog.sqlite3").is_file():
                with _connection(local.root / "catalog.sqlite3") as connection:
                    local_owned = bool(
                        connection.execute(
                            "SELECT 1 FROM teams WHERE id=? AND owner=? UNION ALL "
                            "SELECT 1 FROM team_cleanup WHERE id=? AND owner=?",
                            (team_id, self.owner, team_id, self.owner),
                        ).fetchone()
                    )
            if self.registry.remote is not None and not local_owned:
                deleted = self.registry.remote.deletion_result(team_id, self.owner, operation["id"])
                if deleted is not None:
                    operation["phase"] = "done"
                    self._save(operation)
                    return deleted
            if (local.root / "catalog.sqlite3").is_file():
                with _connection(local.root / "catalog.sqlite3") as connection:
                    pending = connection.execute(
                        "SELECT owner,completed_at FROM team_cleanup WHERE id=?", (team_id,)
                    ).fetchone()
                if pending:
                    if pending[0] != self.owner:
                        raise SwarmAccessError("Team cleanup belongs to a different owner")
                    result = local.delete(team_id, self.owner)
                    operation["phase"] = "done"
                    self._save(operation)
                    return result
            try:
                store = self.registry.open(team_id, self.owner)
            except (SwarmStoreError, sqlite3.DatabaseError):
                store = None
            if store is not None:
                with store._tx(write=True) as connection:
                    if (
                        expected_storage_generation is not None
                        and store._team(connection).get("storage_generation", "")
                        != expected_storage_generation
                    ):
                        raise SwarmConflictError(
                            "The team was restored; refresh before deleting it"
                        )
                    for row in connection.execute(
                        "SELECT id,record FROM publications WHERE state='pending'"
                    ).fetchall():
                        record = json.loads(row[1])
                        record.update(
                            state="revoked", reason="User deleted this team's pending publication"
                        )
                        connection.execute(
                            "UPDATE publications SET state='revoked',record=? WHERE id=?",
                            (_json(record), row[0]),
                        )
                if hasattr(store, "registry") and hasattr(store.registry, "objects"):
                    result = self.registry.remote.delete(
                        team_id,
                        self.owner,
                        request_key=operation["id"],
                        expected_storage_generation=expected_storage_generation,
                    )
                else:
                    result = self.registry.local.delete(team_id, self.owner)
            else:
                # Corrupt bytes cannot supply authority; the owner catalog does.
                local = self.registry.local
                local._assert_visible(team_id, self.owner)
                target = _path(local.root, team_id)
                with _connection(local.root / "catalog.sqlite3", write=True) as connection:
                    connection.execute(
                        "INSERT OR IGNORE INTO team_cleanup VALUES (?,?,?,NULL)",
                        (team_id, self.owner, time.time()),
                    )
                    connection.execute(
                        "DELETE FROM teams WHERE id=? AND owner=?", (team_id, self.owner)
                    )
                if target.exists():
                    shutil.rmtree(target)
                with _connection(local.root / "catalog.sqlite3", write=True) as connection:
                    connection.execute(
                        "UPDATE team_cleanup SET completed_at=? WHERE id=? AND owner=?",
                        (time.time(), team_id, self.owner),
                    )
                result = {"team_id": team_id, "deleted": True}
            operation["phase"] = "done"
            self._save(operation)
            return result

    def retain(self, team_id: str, before_days: int, controller: Any) -> dict[str, str]:
        if not 1 <= before_days <= 36_500:
            raise ValueError("Retention must keep at least one day")
        with self._locked():
            store = self.registry.open(team_id, self.owner)
            before = time.time() - before_days * 86_400
            result = store.retain(controller, before)
            orphan_objects = 0
            if not (hasattr(store, "registry") and hasattr(store.registry, "objects")):
                directory = _path(store.path.parent, "objects")
                if directory.is_dir():
                    with store._tx(write=True) as connection:
                        for path in directory.iterdir():
                            if (
                                not path.is_file()
                                or path.is_symlink()
                                or path.stat().st_mtime >= before
                            ):
                                continue
                            if (
                                _HASH.fullmatch(path.name) or path.name.startswith("pending-")
                            ) and not connection.execute(
                                "SELECT 1 FROM artifacts WHERE object_key=? LIMIT 1", (path.name,)
                            ).fetchone():
                                path.unlink()
                                orphan_objects += 1
            expired_backups = 0
            quarantined_workspaces = 0
            with closing(self._journal()) as connection:
                operations = connection.execute(
                    "SELECT record FROM operations WHERE owner=? AND team_id=? "
                    "AND json_extract(record,'$.phase')='done' "
                    "AND (kind='backup' OR (kind='restore' "
                    "AND json_extract(record,'$.quarantine_id') IS NOT NULL "
                    "AND json_extract(record,'$.quarantine_expired') IS NOT 1 "
                    "AND coalesce(json_extract(record,'$.quarantined_at'),0)<?)) "
                    "AND json_extract(record,'$.created_at')<? LIMIT 2000",
                    (self.owner, team_id, before, before),
                ).fetchall()
            for row in operations:
                operation = json.loads(row[0])
                if operation["kind"] == "backup":
                    _path(self.directory, "exports", operation["id"] + ".zip").unlink(
                        missing_ok=True
                    )
                    operation["phase"] = "expired"
                    self._save(operation)
                    expired_backups += 1
                if operation["kind"] == "restore" and operation.get("quarantine_id"):
                    if operation.get("mode") == "distributed":
                        if self.registry.remote is None:
                            raise SwarmStoreError(
                                "Reconnect distributed storage to prune its quarantine"
                            )
                        with self.registry.remote.database.transaction(write=True) as connection:
                            row = connection.execute(
                                "SELECT schema_name,created_at FROM restore_quarantine "
                                "WHERE request_key=? "
                                "AND owner=? AND team_id=? FOR UPDATE",
                                (operation["id"], self.owner, team_id),
                            ).fetchone()
                            if row:
                                if row[1] >= before:
                                    continue
                                schema = row[0]
                                if not re.fullmatch(r"swarm_quarantine_[a-f0-9]{32}", schema):
                                    raise SwarmAccessError("Invalid quarantine namespace")
                                connection.raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')  # noqa: S608
                                connection.execute(
                                    "DELETE FROM restore_quarantine "
                                    "WHERE request_key=? AND owner=?",
                                    (operation["id"], self.owner),
                                )
                                quarantined_workspaces += 1
                    else:
                        quarantine = _path(self.directory, "quarantine", operation["id"])
                        if quarantine.is_dir():
                            if (
                                operation.get("quarantined_at", quarantine.stat().st_mtime)
                                >= before
                            ):
                                continue
                            shutil.rmtree(quarantine)
                            quarantined_workspaces += 1
                    operation["quarantine_expired"] = True
                    self._save(operation)
            return dict(
                result,
                orphan_objects=str(orphan_objects),
                expired_backups=str(expired_backups),
                quarantined_workspaces=str(quarantined_workspaces),
            )
