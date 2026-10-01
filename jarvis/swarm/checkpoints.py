"""Stable checkpoint identities and bounded history in the existing authority ledger."""

from __future__ import annotations

import json
from typing import Any

from jarvis.core.swarm_types import CheckpointSnapshot

from .store import SwarmConflictError, _digest, _id, _json


def save(
    store: Any,
    connection: Any,
    team: dict[str, Any],
    checkpoint: dict[str, Any],
    *,
    source: str,
    expected_version: int | None = None,
    request_key: str | None = None,
) -> dict[str, Any]:
    """Commit state, identity, history and its event inside the caller's transaction.

    Identical current content is a no-op. Explicit invocation keys retain replay
    protection for the most recent 128 operations; snapshots retain 32 versions.
    """
    content = _json(checkpoint)
    if len(content.encode("utf-8")) > 16_384:
        raise ValueError("Checkpoint exceeds 16 KiB")
    digest = _digest(json.dumps(checkpoint, sort_keys=True, separators=(",", ":")))
    operation_key = None
    if request_key is not None:
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 100:
            raise ValueError("A bounded checkpoint operation key is required")
        operation_key = "checkpoint-operation:" + _digest(request_key)
        previous = connection.execute(
            "SELECT record FROM decisions WHERE request_key=?",
            (operation_key,),
        ).fetchone()
        if previous:
            if json.loads(previous[0])["digest"] != digest:
                raise SwarmConflictError("Checkpoint operation key identifies different content")
            return team
    if expected_version is not None and team["version"] != expected_version:
        raise SwarmConflictError("Checkpoint version changed")
    head_row = connection.execute(
        "SELECT record FROM decisions WHERE request_key='checkpoint-head'"
    ).fetchone()
    head = json.loads(head_row[0]) if head_row else None
    if head is not None and head["digest"] == digest:
        if operation_key is not None:
            _operation(store, connection, operation_key, head, digest)
        return team
    identity = head["id"] if head else _id()
    version = str(int(head["version"]) + 1) if head else "1"
    snapshot_id = _id()
    snapshot = CheckpointSnapshot.model_validate(
        {
            "id": snapshot_id,
            "kind": "checkpoint_snapshot",
            "team_id": store.team_id,
            "checkpoint_id": identity,
            "version": version,
            "digest": digest,
            "source": source[:80],
            "created_at": store.clock(),
            "checkpoint": checkpoint,
        }
    ).model_dump(mode="json")
    connection.execute(
        "INSERT INTO decisions (id,request_key,record) VALUES (?,?,?)",
        (snapshot_id, f"checkpoint:{identity}:{version}", _json(snapshot)),
    )
    head = {key: value for key, value in snapshot.items() if key != "checkpoint"}
    head.update(id=identity, kind="checkpoint_head", snapshot_id=snapshot_id)
    if head_row:
        connection.execute("UPDATE decisions SET record=? WHERE id=?", (_json(head), identity))
    else:
        connection.execute(
            "INSERT INTO decisions (id,request_key,record) VALUES (?,'checkpoint-head',?)",
            (identity, _json(head)),
        )
    if operation_key is not None:
        _operation(store, connection, operation_key, head, digest)
    _retain(connection, "checkpoint_snapshot", 32)
    team.update(checkpoint=checkpoint, version=team["version"] + 1)
    store._save_team(connection, team)
    store._event(
        connection,
        "team.checkpoint",
        "Versioned team checkpoint saved",
        data={
            "checkpoint_id": identity,
            "checkpoint_version": version,
            "snapshot_id": snapshot_id,
            "source": source[:80],
        },
    )
    return team


def _operation(store: Any, connection: Any, key: str, head: dict[str, Any], digest: str) -> None:
    record = {
        "id": _id(),
        "kind": "checkpoint_operation",
        "team_id": store.team_id,
        "checkpoint_id": head["id"],
        "version": head["version"],
        "digest": digest,
    }
    connection.execute(
        "INSERT INTO decisions (id,request_key,record) VALUES (?,?,?)",
        (record["id"], key, _json(record)),
    )
    _retain(connection, "checkpoint_operation", 128)


def _retain(connection: Any, kind: str, count: int) -> None:
    rows = connection.execute(
        "SELECT id FROM decisions WHERE json_extract(record,'$.kind')=? "
        "ORDER BY rowid DESC LIMIT 200 OFFSET ?",
        (kind, count),
    ).fetchall()
    connection.executemany("DELETE FROM decisions WHERE id=?", [(row[0],) for row in rows])
