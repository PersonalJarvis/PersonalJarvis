"""Consistent, portable PostgreSQL snapshots with content-verified S3 objects.

An export uses a read-only repeatable-read snapshot so all rows and object
references describe one version without excluding writers. Restore commits a new
protected namespace as a
single transaction and rotates every live controller/attempt credential.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, SwarmStoreError, _digest, _json

from .database import team_schema
from .registry import SCHEMA_VERSION, TABLES


def _file(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink() or path.resolve().parent != root:
        raise SwarmAccessError("Snapshot file escaped its selected directory")
    return path


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_rows(snapshot):
    with _file(snapshot, "artifacts.jsonl").open("rb") as stream:
        columns = json.loads(stream.readline(16385))["columns"]
        for line in iter(lambda: stream.readline(16_000_001), b""):
            if len(line) > 16_000_000:
                raise SwarmStoreError("Snapshot artifact row exceeds its limit")
            yield dict(zip(columns, json.loads(line), strict=True))["record"]


def snapshot_columns():
    """Read column order from the shipped DDL, without opening PostgreSQL."""
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    schema = schema.replace(", __swarm_order BIGINT GENERATED ALWAYS AS IDENTITY", "").replace(
        "GENERATED ALWAYS AS IDENTITY", ""
    )
    with closing(sqlite3.connect(":memory:")) as prototype:
        prototype.executescript(schema)
        columns = {
            table: [row[1] for row in prototype.execute(f'PRAGMA table_info("{table}")')]
            + ([] if table == "events" else ["__swarm_order"])
            for table in TABLES
            if table not in {"delivery_outbox", "object_uploads"}
        }
    columns["delivery_outbox"] = [
        "event_id",
        "event_seq",
        "published_at",
        "attempts",
        "acknowledged_at",
    ]
    columns["object_uploads"] = [
        "id",
        "owner_id",
        "task_id",
        "fingerprint",
        "size_bytes",
        "state",
        "created_at",
        "task_fence",
        "sha256",
        "name",
        "media_type",
        "request_key",
        "recovered_artifact_id",
    ]
    return columns


def validate_snapshot(root, namespace):
    """Check all table hashes, column layouts, bounded rows and scoped objects offline."""
    from jarvis.core.swarm_types import TeamRecord

    root = Path(root).resolve(strict=True)
    manifest_file = _file(root, "manifest.json")
    if manifest_file.stat().st_size > 32768:
        raise SwarmStoreError("Snapshot manifest exceeds its size limit")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    team_id = manifest.get("team_id", "")
    team_schema(namespace, team_id)
    if (
        manifest.get("format") != "jarvis-swarm-postgresql"
        or manifest.get("schema_version") != SCHEMA_VERSION
    ):
        raise SwarmStoreError("Snapshot requires a compatible distributed application version")
    if set(manifest.get("files", {})) != {table + ".jsonl" for table in TABLES}:
        raise SwarmStoreError("Snapshot table inventory is incomplete")
    expected_columns = snapshot_columns()
    for table in TABLES:
        name = table + ".jsonl"
        path = _file(root, name)
        if _sha(path) != manifest["files"][name]:
            raise SwarmStoreError("Snapshot table hash mismatch")
        with path.open("rb") as stream:
            columns = json.loads(stream.readline(16385))["columns"]
            if columns != expected_columns[table]:
                raise SwarmStoreError("Snapshot columns differ from the supported schema")
            count = 0
            for line in iter(lambda stream=stream: stream.readline(16_000_001), b""):
                if len(line) > 16_000_000:
                    raise SwarmStoreError("Snapshot row exceeds its size limit")
                values = json.loads(line)
                if not isinstance(values, list) or len(values) != len(columns):
                    raise SwarmStoreError("Snapshot row has invalid column count")
                count += 1
                if "record" in columns:
                    record = values[columns.index("record")]
                    if not isinstance(record, dict) or record.get("team_id", team_id) != team_id:
                        raise SwarmAccessError("Snapshot record belongs to another team")
                    if table == "team":
                        team = TeamRecord.model_validate(record)
                        if team.id != team_id or team.version != manifest["team_version"]:
                            raise SwarmStoreError("Snapshot team identity/version mismatch")
                    if table == "artifacts":
                        artifact = _file(root / "objects", record["object_key"])
                        if (
                            record["object_key"] != record["sha256"]
                            or artifact.stat().st_size != int(record["size_bytes"])
                            or artifact.stat().st_size > 10_000_000
                            or _sha(artifact) != record["sha256"]
                        ):
                            raise SwarmStoreError("Snapshot artifact failed size/hash validation")
            if str(count) != manifest.get("counts", {}).get(table):
                raise SwarmStoreError("Snapshot row count does not match its manifest")
    return manifest


def backup(store, destination):
    requested = Path(destination)
    if requested.is_symlink():
        raise SwarmAccessError("Snapshot destination may not be a filesystem link")
    target = requested.resolve()
    if target.exists():
        raise SwarmConflictError("Select a new snapshot directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="swarm-export-", dir=target.parent) as temp:
        staging = Path(temp).resolve()
        (staging / "objects").mkdir()
        with store._tx() as connection:
            team = store._team(connection)
            manifest = dict(
                format="jarvis-swarm-postgresql",
                schema_version=SCHEMA_VERSION,
                team_id=store.team_id,
                team_version=team["version"],
                created_at=store.clock(),
                files={},
                counts={},
            )
            for table in TABLES:
                path = staging / (table + ".jsonl")
                count = 0
                with (
                    connection.raw.cursor(name="export_" + table) as cursor,
                    path.open("w", encoding="utf-8") as output,
                ):
                    cursor.execute(f'SELECT * FROM "{table}"')  # noqa: S608
                    # Named cursor metadata becomes available after the first fetch.
                    batch = cursor.fetchmany(500)
                    names = [column.name for column in cursor.description]
                    output.write(_json({"columns": names}) + "\n")
                    while batch:
                        for values in batch:
                            output.write(_json(list(values)) + "\n")
                            count += 1
                        batch = cursor.fetchmany(500)
                    output.flush()
                    os.fsync(output.fileno())
                manifest["files"][table + ".jsonl"] = _sha(path)
                manifest["counts"][table] = str(count)
            manifest["artifacts"] = manifest["counts"]["artifacts"]
            (staging / "manifest.json").write_text(_json(manifest), encoding="utf-8")
        # All references are now captured. Immutable objects can stream after
        # the MVCC snapshot closes, without retaining a PostgreSQL connection.
        from jarvis.swarm.streams import open_artifact

        for record in _artifact_rows(staging):
            source = open_artifact(store, record)
            target_object = _file(staging / "objects", record["object_key"])
            digest = hashlib.sha256()
            copied = 0
            try:
                with target_object.open("wb") as output:
                    for chunk in iter(lambda source=source: source.read(65536), b""):
                        copied += len(chunk)
                        if copied > int(record["size_bytes"]):
                            raise SwarmStoreError("Snapshot object exceeds its verified size")
                        digest.update(chunk)
                        output.write(chunk)
            finally:
                source.close()
            if copied != int(record["size_bytes"]) or digest.hexdigest() != record["sha256"]:
                raise SwarmStoreError("Snapshot object failed content validation")
        # Atomic directory rename exposes only a completely written snapshot.
        os.replace(staging, target)
    return manifest


def _load_rows(store, connection, root, manifest, object_versions):
    registry, team_id, raw = store.registry, store.team_id, connection.raw
    for table in TABLES:
        with _file(root, table + ".jsonl").open("rb") as stream:
            header = json.loads(stream.readline(16_385))
            names = header["columns"]
            columns = [
                row[0]
                for row in raw.execute(
                    (
                        "SELECT column_name FROM information_schema.columns WHERE "
                        "table_schema=%s AND table_name=%s ORDER BY ordinal_position"
                    ),
                    (team_schema(registry.config.namespace, team_id), table),
                )
            ]
            if names != columns:
                raise SwarmStoreError("Snapshot columns differ from the supported schema")
            names_sql = ",".join('"' + name + '"' for name in columns)
            query = (
                f'INSERT INTO "{table}" ({names_sql}) OVERRIDING SYSTEM VALUE VALUES ('  # noqa: S608
                + ",".join(["%s"] * len(columns))
                + ")"
            )  # noqa: S608
            count = 0
            for line in iter(lambda stream=stream: stream.readline(16_000_001), b""):
                if len(line) > 16_000_000:
                    raise SwarmStoreError("Snapshot row exceeds its size limit")
                values = json.loads(line)
                if len(values) != len(columns):
                    raise SwarmStoreError("Snapshot row has invalid column count")
                if "record" in columns:
                    index = columns.index("record")
                    record = values[index]
                    if table == "artifacts":
                        record.update(object_versions[record["object_key"]])
                    values[index] = _json(record)
                raw.execute(query, values)
                count += 1
            if str(count) != manifest["counts"][table]:
                raise SwarmStoreError("Snapshot row count does not match its manifest")
        sequence_column = "seq" if table == "events" else "__swarm_order"
        if sequence_column in columns:
            raw.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}','{sequence_column}'), "  # noqa: S608
                f"coalesce(max({sequence_column}),1), "
                f"max({sequence_column}) IS NOT NULL) FROM {table}"
            )  # noqa: S608
    raw.execute("SET CONSTRAINTS ALL IMMEDIATE")


class _ValidatedRollback(SwarmStoreError):
    """Internal signal: roll back the isolated validation schema after all checks."""


def validate_database_rows(registry, root, *, owner="local-user"):
    """Apply the exact PostgreSQL constraints in a rollback-only temporary namespace.

    No live team/catalog rows, objects or credentials are mutated. Connection
    loss also rolls back the temporary schema and every validation row.
    """
    root = Path(root).resolve(strict=True)
    manifest = validate_snapshot(root, registry.config.namespace)
    isolated = copy.copy(registry)
    isolated.config = registry.config.model_copy(
        update={"namespace": "check_" + secrets.token_hex(6)}
    )
    versions = {record["object_key"]: {} for record in _artifact_rows(root)}
    try:
        with registry.database.transaction(write=True) as connection:
            registry.database.validate_role(connection.raw)
            store = isolated._store(manifest["team_id"], owner)
            store._create_schema(connection)
            _load_rows(store, connection, root, manifest, versions)
            raise _ValidatedRollback("Snapshot constraints passed")
    except _ValidatedRollback:
        return
    except SwarmConflictError as exc:
        raise SwarmStoreError(
            "Backup violates database row constraints; select a valid snapshot"
        ) from exc


def restore(registry, source, *, owner, request_key, replace_existing=False):
    requested = Path(source)
    if requested.is_symlink():
        raise SwarmAccessError("Snapshot source may not be a filesystem link")
    root = requested.resolve(strict=True)
    validate_snapshot(root, registry.config.namespace)
    validate_database_rows(registry, root, owner=owner)
    manifest_file = _file(root, "manifest.json")
    if manifest_file.stat().st_size > 32_768:
        raise SwarmStoreError("Snapshot manifest exceeds its size limit")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    team_id = manifest.get("team_id", "")
    team_schema(registry.config.namespace, team_id)
    if (
        manifest.get("format") != "jarvis-swarm-postgresql"
        or manifest.get("schema_version") != SCHEMA_VERSION
    ):
        raise SwarmStoreError("Snapshot requires a compatible distributed application version")
    if not owner or len(owner) > 200 or not request_key or len(request_key) > 100:
        raise ValueError("Restore requires bounded owner and operation identities")
    expected = {table + ".jsonl" for table in TABLES}
    if set(manifest.get("files", {})) != expected:
        raise SwarmStoreError("Snapshot table inventory is incomplete")
    for name, digest in manifest["files"].items():
        if _sha(_file(root, name)) != digest:
            raise SwarmStoreError("Snapshot table hash mismatch")
    registry.provision()
    fingerprint = "backup:" + _digest(json.dumps(manifest, sort_keys=True))
    with registry.database.transaction() as connection:
        registered = connection.execute(
            "SELECT owner,request_key,spec_hash FROM teams WHERE id=?", (team_id,)
        ).fetchone()
        if registered and registered["owner"] != owner:
            raise SwarmAccessError("Existing team belongs to a different owner")
        if registered and registered["request_key"] == request_key:
            if registered["spec_hash"] != fingerprint:
                raise SwarmConflictError("Restore key already identifies another snapshot")
            already_restored = True
        else:
            already_restored = False
            if registered and not replace_existing:
                raise SwarmConflictError("Team already exists; explicitly confirm replacement")
            if not registered and replace_existing:
                raise SwarmAccessError("Replacement requires an existing owned team")
    if already_restored:
        return registry.open(team_id, owner).get()
    object_versions = {}
    objects_dir = _file(root, "objects").resolve(strict=True)
    # Upload immutable objects before taking the catalog/schema transaction.
    # Retry is conditional and idempotent; no network I/O holds authority locks.
    for record in _artifact_rows(root):
        if record.get("team_id") != team_id or record["object_key"] != record["sha256"]:
            raise SwarmAccessError("Snapshot artifact belongs to another namespace")
        artifact_file = _file(objects_dir, record["object_key"])
        if artifact_file.stat().st_size != int(record["size_bytes"]):
            raise SwarmStoreError("Snapshot artifact size mismatch")
        object_versions[record["object_key"]] = registry.objects.put_file(
            team_id, record["sha256"], artifact_file
        )
    with registry.database.transaction(write=True) as connection:
        raw = connection.raw
        connection.execute("SELECT singleton FROM registry_lock WHERE singleton=1 FOR UPDATE")
        existing = connection.execute(
            "SELECT id,spec_hash FROM teams WHERE owner=? AND request_key=?", (owner, request_key)
        ).fetchone()
        if existing:
            if existing["id"] != team_id or existing["spec_hash"] != fingerprint:
                raise SwarmConflictError("Restore key already identifies another snapshot")
        else:
            previous = connection.execute(
                "SELECT * FROM teams WHERE id=? FOR UPDATE", (team_id,)
            ).fetchone()
            if previous:
                if previous["owner"] != owner:
                    raise SwarmAccessError("Existing team belongs to a different owner")
                if not replace_existing:
                    raise SwarmConflictError("Team already exists; explicitly confirm replacement")
                schema = team_schema(registry.config.namespace, team_id)
                quarantine = "swarm_quarantine_" + _digest(schema + request_key)[:32]
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS restore_quarantine (request_key TEXT PRIMARY "
                    "KEY, owner TEXT NOT NULL, team_id TEXT NOT NULL, schema_name TEXT NOT NULL, "
                    "created_at DOUBLE PRECISION NOT NULL, catalog_record JSONB NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO restore_quarantine VALUES (?,?,?,?,?,?)",
                    (
                        request_key,
                        owner,
                        team_id,
                        quarantine,
                        registry.clock(),
                        _json(dict(previous)),
                    ),
                )
                raw.execute(f'ALTER SCHEMA "{schema}" RENAME TO "{quarantine}"')  # noqa: S608
                connection.execute("DELETE FROM teams WHERE id=? AND owner=?", (team_id, owner))
            elif replace_existing:
                raise SwarmAccessError("Replacement requires an existing owned team")
            connection.execute(
                "INSERT INTO teams VALUES (?,?,?,?,?,?,?)",
                (
                    team_id,
                    owner,
                    request_key,
                    "Restoring team",
                    registry.clock(),
                    fingerprint,
                    SCHEMA_VERSION,
                ),
            )
            store = registry._store(team_id, owner)
            store._create_schema(connection)
            _load_rows(store, connection, root, manifest, object_versions)
            if store._team(connection)["version"] != manifest["team_version"]:
                raise SwarmStoreError("Snapshot team version disagrees with manifest")
            store._suspend_attempts(connection, "Restored from consistent distributed snapshot")
            connection.execute(
                "UPDATE controller SET expires_at=0,fence=fence+1,token_hash=? WHERE singleton=1",
                (_digest(secrets.token_urlsafe(32)),),
            )
            for row in connection.execute("SELECT id FROM agents").fetchall():
                connection.execute(
                    "UPDATE agents SET token_hash=? WHERE id=?",
                    (_digest(secrets.token_urlsafe(32)), row[0]),
                )
            team = store._team(connection)
            team["storage_generation"] = _digest(request_key + fingerprint)[:32]
            if team["state"] not in {"succeeded", "failed", "canceled", "archived"}:
                preparation = team.get("checkpoint", {}).get("preparation", {})
                team.update(
                    state="created"
                    if preparation.get("required") and preparation.get("state") != "launched"
                    else "paused",
                    reason="Restored backup; review before resuming",
                )
            store._save_team(connection, team)
            store._event(
                connection, "storage.restored", "Team restored; execution credentials fenced"
            )
            raw.execute(
                f'UPDATE "{registry.config.namespace}".teams SET name=%s WHERE id=%s',  # noqa: S608
                (store._team(connection)["name"], team_id),
            )
    return registry.open(team_id, owner).get()
