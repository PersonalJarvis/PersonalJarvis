"""Immutable content-addressed local artifacts and durable publication outbox."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from jarvis.core.swarm_types import SwarmActor, SwarmController, decimal_counter

if TYPE_CHECKING:
    from jarvis.swarm.store import TeamStore


class ObjectMixin:
    def write_artifact(
        self,
        actor: SwarmActor,
        name: str,
        content: str | bytes,
        request_key: str,
        *,
        media_type: str = "text/plain",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        return self._write_artifact(
            actor,
            name,
            content,
            request_key,
            media_type=media_type,
            expected_version=expected_version,
        )

    def capture_result(
        self,
        controller: SwarmController,
        actor: SwarmActor,
        name: str,
        content: str | bytes,
        request_key: str,
        media_type: str = "text/plain",
    ) -> dict[str, Any]:
        """Capture worker output without granting it trusted receipt provenance."""
        return self._write_artifact(
            actor, name, content, request_key, media_type=media_type, controller=controller
        )

    def capture_receipt(
        self,
        controller: SwarmController,
        actor: SwarmActor,
        kind: str,
        payload: dict[str, Any],
        subject_ids: list[str],
        request_key: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist host-observed evidence with controller and attempt authority."""
        from jarvis.swarm.receipts import build_receipt, canonical

        store = cast("TeamStore", self)
        with store._tx() as connection:
            store._controller(connection, controller)
            store._actor(connection, actor, writing=True)
        receipt = build_receipt(controller, actor, kind, payload, subject_ids, trace_id)
        return self._write_artifact(
            actor,
            f"{kind}.json",
            canonical(receipt),
            request_key,
            media_type="application/json",
            controller=controller,
            receipt=receipt,
        )

    def _write_artifact(
        self,
        actor: SwarmActor,
        name: str,
        content: str | bytes,
        request_key: str,
        *,
        media_type: str = "text/plain",
        expected_version: int | None = None,
        controller: SwarmController | None = None,
        receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from jarvis.swarm.store import (
            SwarmAccessError,
            SwarmBudgetError,
            SwarmConflictError,
            _id,
            _json,
        )

        if not name or len(name) > 200 or not request_key or len(request_key) > 100:
            raise ValueError("Artifact requires bounded name and operation key")
        if len(media_type) > 100 or any(character in media_type for character in "\r\n"):
            raise ValueError("Invalid artifact media type")
        if expected_version not in {None, 1}:
            raise SwarmConflictError("Artifacts are immutable; write a new version under a new key")
        data = content.encode("utf-8") if isinstance(content, str) else content
        if len(data) > 10_000_000:
            raise SwarmBudgetError("An artifact may contain at most 10 MB")
        digest = hashlib.sha256(data).hexdigest()
        origin = "worker-authored" if controller is None else "worker-output"
        receipt_source = None
        if receipt is not None:
            from jarvis.swarm.receipts import build_receipt, canonical, provenance

            if controller is None:
                raise SwarmAccessError("Runtime receipts require trusted controller authority")
            checked = build_receipt(
                controller,
                actor,
                receipt["kind"],
                receipt["payload"],
                receipt["subject_ids"],
                receipt["trace_id"],
            )
            if checked != receipt or canonical(checked).encode("utf-8") != data:
                raise SwarmAccessError("Runtime receipt content and scope bindings disagree")
            receipt_source = provenance(checked)
            origin = "runtime-receipt"
        store = cast("TeamStore", self)
        with store._tx(write=True) as connection:
            store._actor(connection, actor, writing=True)
            if controller is None:
                store._require_tool(connection, "write_artifact")
            else:
                store._controller(connection, controller)
            if not actor.task_id:
                raise SwarmAccessError("Artifacts require an active task attempt")
            if receipt_source is not None:
                self._receipt_subjects(connection, actor, receipt)
            existing = connection.execute(
                "SELECT record FROM artifacts WHERE owner_id=? AND request_key=?",
                (actor.agent_id, request_key),
            ).fetchone()
            if existing:
                record = json.loads(existing[0])
                if (
                    record["sha256"] != digest
                    or record["name"] != name
                    or record["task_id"] != actor.task_id
                    or record["attempt_fence"] != actor.task_fence
                    or record["media_type"] != media_type
                    or record.get("provenance", {}).get("origin", "worker-authored") != origin
                    or (receipt_source is not None and record.get("provenance") != receipt_source)
                ):
                    raise SwarmConflictError(
                        "Artifact operation key already identifies different content"
                    )
                return record
            current = connection.execute(
                "SELECT value FROM counters WHERE name='artifact_bytes'"
            ).fetchone()
            total = int(current[0]) if current else 0
            if total + len(data) > int(store._team(connection)["policy"]["max_artifact_bytes"]):
                raise SwarmBudgetError("Team artifact storage budget exhausted")
            location = self._put_object(data, digest)
            artifact_id = _id()
            record = dict(
                id=artifact_id,
                team_id=store.team_id,
                owner_id=actor.agent_id,
                task_id=actor.task_id,
                attempt_fence=actor.task_fence,
                name=name,
                media_type=media_type,
                sha256=digest,
                size_bytes=str(len(data)),
                object_key=digest,
                version=1,
                created_at=store.clock(),
                provenance={
                    "origin": origin,
                    "source_agent_id": actor.agent_id,
                    "source_task_id": actor.task_id,
                    "source_attempt": actor.task_fence,
                },
            )
            record.update(location)
            # Storage location adapters cannot assign or replace evidence authority.
            record["provenance"] = receipt_source or {
                "origin": origin,
                "source_agent_id": actor.agent_id,
                "source_task_id": actor.task_id,
                "source_attempt": actor.task_fence,
            }
            connection.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,0,?)",
                (
                    artifact_id,
                    actor.agent_id,
                    actor.task_id,
                    digest,
                    digest,
                    str(len(data)),
                    request_key,
                    _json(record),
                ),
            )
            connection.execute(
                "INSERT INTO counters VALUES ('artifact_bytes',?) ON CONFLICT(name) DO UPDATE "
                "SET value=excluded.value",
                (str(total + len(data)),),
            )
            store._event(
                connection,
                "artifact.created",
                name,
                agent_id=actor.agent_id,
                task_id=actor.task_id,
                data={"artifact_id": artifact_id, "sha256": digest, "size_bytes": str(len(data))},
            )
            return record

    def _receipt_subjects(self, connection, actor, receipt) -> None:
        """Check source references in the same transaction that commits a receipt."""
        from jarvis.swarm.store import SwarmAccessError, _ancestor_ids

        store = cast("TeamStore", self)
        store._task(connection, actor.task_id)
        ancestors = _ancestor_ids(connection, actor.task_id)
        hashes = set()
        for subject in receipt["subject_ids"]:
            row = connection.execute(
                "SELECT record FROM artifacts WHERE id=?", (subject,)
            ).fetchone()
            if row is None:
                raise SwarmAccessError("Receipt subject is unavailable in this team")
            source = json.loads(row[0])
            if (
                source["team_id"] == actor.team_id
                and source["task_id"] == actor.task_id
                and source["owner_id"] == actor.agent_id
                and source["attempt_fence"] == actor.task_fence
            ):
                hashes.add(source["sha256"])
                continue
            if source["task_id"] in ancestors:
                dependency = store._task(connection, source["task_id"])
                if dependency["state"] == "succeeded" and subject in dependency["evidence"]:
                    hashes.add(source["sha256"])
                    continue
            raise SwarmAccessError(
                "Receipt subject must bind this attempt or an accepted dependency"
            )
        if receipt["kind"] == "http" and receipt["payload"]["body_sha256"] not in hashes:
            raise SwarmAccessError("HTTP receipt must identify its captured response body")

    def _put_object(self, data: bytes, digest: str) -> dict[str, Any]:
        """Persist immutable bytes; storage adapters may return location metadata."""
        from jarvis.swarm.store import SwarmAccessError, SwarmConflictError

        store = cast("TeamStore", self)
        object_dir = store.path.parent / "objects"
        object_dir.mkdir(exist_ok=True)
        if object_dir.is_symlink() or object_dir.resolve().parent != store.path.parent.resolve():
            raise SwarmAccessError("Artifact storage path escaped its team directory")
        target = object_dir / digest
        if target.exists():
            if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise SwarmConflictError(
                    "Existing content-addressed object failed integrity validation"
                )
        else:
            handle, temporary = tempfile.mkstemp(prefix="pending-", dir=object_dir)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return {}

    def _read_object(self, record: dict[str, Any]) -> bytes:
        """Read bounded bytes from the selected immutable object location."""
        from jarvis.swarm.store import SwarmAccessError, SwarmStoreError

        store = cast("TeamStore", self)
        key = record["object_key"]
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
            raise SwarmAccessError("Invalid team object key")
        directory = store.path.parent / "objects"
        path = directory / key
        if (
            directory.is_symlink()
            or directory.resolve().parent != store.path.parent.resolve()
            or path.is_symlink()
            or path.resolve().parent != directory
        ):
            raise SwarmAccessError("Object resolved outside its team namespace")
        try:
            with path.open("rb") as stream:
                data = stream.read(10_000_001)
        except FileNotFoundError as error:
            raise SwarmStoreError(
                "Artifact object is missing; restore the team's consistent backup"
            ) from error
        return data

    def _artifact_content(
        self, connection: sqlite3.Connection, artifact_id: str
    ) -> tuple[dict[str, Any], bytes]:
        from jarvis.swarm.store import SwarmAccessError, SwarmStoreError

        row = connection.execute(
            "SELECT record FROM artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise SwarmAccessError("Artifact unavailable in this team")
        record = json.loads(row[0])
        data = self._read_object(record)
        if (
            len(data) != int(record["size_bytes"])
            or hashlib.sha256(data).hexdigest() != record["sha256"]
        ):
            raise SwarmStoreError("Artifact failed size/hash verification; restore its backup")
        return record, data

    def read_artifact(self, actor: SwarmActor, artifact_id: str) -> dict[str, Any]:
        store = cast("TeamStore", self)
        with store._tx() as connection:
            store._actor(connection, actor)
            store._require_tool(connection, "read_artifact")
            record, data = self._artifact_content(connection, artifact_id)
            return dict(record, content=data.decode("utf-8", errors="replace"))

    def download_artifact(self, artifact_id: str) -> tuple[dict[str, Any], bytes]:
        """Owner-authorized registry handle only; worker downloads use read_artifact."""
        store = cast("TeamStore", self)
        with store._tx() as connection:
            store._team(connection)
            return self._artifact_content(connection, artifact_id)

    def consume_network(
        self, actor: SwarmActor, request_key: str, amount: str | int
    ) -> dict[str, Any]:
        from jarvis.swarm.store import SwarmAccessError, SwarmBudgetError, SwarmConflictError

        amount = decimal_counter(amount)
        if not request_key or len(request_key) > 100:
            raise ValueError("A bounded network operation key is required")
        store = cast("TeamStore", self)
        with store._tx(write=True) as connection:
            store._actor(connection, actor, writing=True)
            if not actor.task_id:
                raise SwarmAccessError("Network usage requires a task attempt")
            policy = store._team(connection)["policy"]
            if not policy["internet"]:
                raise SwarmAccessError("Team internet access is disabled")
            key = (
                "network:"
                + actor.agent_id
                + ":"
                + actor.task_id
                + ":"
                + str(actor.task_fence)
                + ":"
                + request_key
            )
            existing = connection.execute(
                "SELECT value FROM counters WHERE name=?", (key,)
            ).fetchone()
            if existing:
                if existing[0] != amount:
                    raise SwarmConflictError("Network operation already charged a different amount")
                return dict(id=key, amount=amount, duplicate=True)
            team = store._team(connection)
            total = int(team["network_bytes"]) + int(amount)
            if total > int(team["policy"]["max_network_bytes"]):
                raise SwarmBudgetError("Team network byte budget exhausted")
            team["network_bytes"] = str(total)
            store._save_team(connection, team)
            connection.execute("INSERT INTO counters VALUES (?,?)", (key, amount))
            return dict(id=key, amount=amount, duplicate=False)

    def authorize_destination(
        self,
        destination_id: str,
        *,
        kind: str = "artifact",
        label: str = "",
        expected_version: int = 1,
        expected_team_version: int | None = None,
        expected_storage_generation: str | None = None,
    ) -> dict[str, Any]:
        """Trusted user control plane records an explicit destination grant."""
        from jarvis.swarm.store import SwarmConflictError, _check_expected, _json

        if (
            not destination_id
            or len(destination_id) > 200
            or kind not in {"artifact", "conversation", "reviewed_knowledge"}
        ):
            raise ValueError("Select an explicit approved publication destination")
        store = cast("TeamStore", self)
        with store._tx(write=True) as connection:
            _check_expected(
                store._team(connection), expected_team_version, expected_storage_generation
            )
            existing = connection.execute(
                "SELECT record FROM destinations WHERE id=?", (destination_id,)
            ).fetchone()
            if existing:
                record = json.loads(existing[0])
                if record["version"] != expected_version or record["kind"] != kind:
                    raise SwarmConflictError("Publication destination grant changed")
                return record
            if expected_version != 1:
                raise SwarmConflictError("New destination grant starts at version one")
            record = dict(
                id=destination_id,
                team_id=store.team_id,
                kind=kind,
                label=label[:200],
                version=1,
                active=True,
            )
            connection.execute(
                "INSERT INTO destinations VALUES (?,?)", (destination_id, _json(record))
            )
            store._event(
                connection,
                "publication.destination",
                "Publication destination authorized",
                data={"destination_id": destination_id},
            )
            return record

    def revoke_destination(
        self, destination_id: str, *, expected_version: int = 1
    ) -> dict[str, Any]:
        """Owner-authorized grant revocation fences every not-yet-published intent."""
        from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, _json

        store = cast("TeamStore", self)
        with store._tx(write=True) as connection:
            row = connection.execute(
                "SELECT record FROM destinations WHERE id=?", (destination_id,)
            ).fetchone()
            if row is None:
                raise SwarmAccessError("Destination unavailable in this team")
            record = json.loads(row[0])
            if record["version"] != expected_version:
                raise SwarmConflictError("Publication destination grant changed")
            record.update(active=False, version=record["version"] + 1)
            connection.execute(
                "UPDATE destinations SET record=? WHERE id=?", (_json(record), destination_id)
            )
            for row in connection.execute(
                "SELECT record FROM publications WHERE destination_id=? AND state='pending'",
                (destination_id,),
            ).fetchall():
                publication = json.loads(row[0])
                publication["state"] = "revoked"
                connection.execute(
                    "UPDATE publications SET state='revoked',record=? WHERE id=?",
                    (_json(publication), publication["id"]),
                )
            store._event(
                connection,
                "publication.revoked",
                "Publication destination grant revoked",
                data={"destination_id": destination_id},
            )
            return record

    def queue_owner_publication(
        self,
        controller: SwarmController,
        artifact_id: str,
        destination_id: str,
        request_key: str,
        **conditions: Any,
    ) -> dict[str, Any]:
        """Publish an owner's selection through the persistent lead without rotating its token."""
        return self.queue_publication(
            controller,
            None,
            artifact_id,
            destination_id,
            request_key,
            _owner_control=True,
            **conditions,
        )

    def queue_publication(
        self,
        controller: SwarmController,
        lead_actor: SwarmActor | None,
        artifact_id: str,
        destination_id: str,
        request_key: str,
        expected_version: int = 1,
        expected_team_version: int | None = None,
        expected_storage_generation: str | None = None,
        _owner_control: bool = False,
    ) -> dict[str, Any]:
        from jarvis.swarm.store import (
            SwarmAccessError,
            SwarmConflictError,
            _actor_digest,
            _check_expected,
            _id,
            _json,
        )

        if not request_key or len(request_key) > 100:
            raise ValueError("A bounded publication idempotency key is required")
        store = cast("TeamStore", self)
        with store._tx(write=True) as connection:
            store._controller(connection, controller)
            team = store._team(connection)
            _check_expected(team, expected_team_version, expected_storage_generation)
            principal = team["lead_id"] if _owner_control else getattr(lead_actor, "agent_id", None)
            member = connection.execute(
                "SELECT * FROM agents WHERE id=? AND active=1", (principal,)
            ).fetchone()
            if (
                member is None
                or principal != team["lead_id"]
                or member["role"] != "lead"
                or (
                    not _owner_control
                    and (
                        lead_actor is None
                        or lead_actor.team_id != store.team_id
                        or not hmac.compare_digest(member["token_hash"], _actor_digest(lead_actor))
                    )
                )
            ):
                raise SwarmAccessError(
                    "Only the scoped team lead may publish selected accepted artifacts"
                )
            row = connection.execute(
                "SELECT record FROM destinations WHERE id=?", (destination_id,)
            ).fetchone()
            if row is None:
                raise SwarmAccessError("Destination was not explicitly authorized")
            destination = json.loads(row[0])
            if not destination["active"] or destination["version"] != expected_version:
                raise SwarmAccessError("Destination grant revoked or stale")
            existing = connection.execute(
                "SELECT record FROM publications WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing:
                record = json.loads(existing[0])
                if (
                    record["artifact_id"] != artifact_id
                    or record["destination_id"] != destination_id
                    or record["destination_version"] != expected_version
                ):
                    raise SwarmConflictError("Publication key already identifies different output")
                return record
            artifact, _ = self._artifact_content(connection, artifact_id)
            task = store._task(connection, artifact["task_id"])
            if task["state"] != "succeeded" or artifact_id not in task["evidence"]:
                raise SwarmAccessError("Only accepted task evidence may leave the team")
            record = dict(
                id=_id(),
                team_id=store.team_id,
                artifact_id=artifact_id,
                destination_id=destination_id,
                destination_version=expected_version,
                request_key=request_key,
                state="pending",
                sha256=artifact["sha256"],
                created_at=store.clock(),
                receipt=None,
            )
            connection.execute(
                "INSERT INTO publications VALUES (?,?,?,?,?,?)",
                (record["id"], request_key, destination_id, artifact_id, "pending", _json(record)),
            )
            connection.execute("UPDATE artifacts SET pinned=1 WHERE id=?", (artifact_id,))
            store._event(
                connection,
                "publication.queued",
                "Accepted artifact queued for its approved destination",
                agent_id=principal,
                data={"publication_id": record["id"], "artifact_id": artifact_id},
            )
            return record

    def pending_publications(
        self, controller: SwarmController, limit: int = 50
    ) -> list[dict[str, Any]]:
        from jarvis.swarm.store import _page

        _page(limit)
        store = cast("TeamStore", self)
        with store._tx() as connection:
            store._controller(connection, controller)
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT record FROM publications WHERE state='pending' ORDER BY rowid LIMIT ?",
                    (limit,),
                )
            ]

    def complete_publication(
        self, controller: SwarmController, publication_id: str, receipt: dict[str, Any]
    ) -> dict[str, Any]:
        from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, _json

        if (
            not receipt.get("destination_key")
            or not receipt.get("sha256")
            or len(_json(receipt).encode("utf-8")) > 4096
        ):
            raise ValueError("A bounded durable destination receipt is required")
        store = cast("TeamStore", self)
        with store._tx(write=True) as connection:
            store._controller(connection, controller)
            row = connection.execute(
                "SELECT record FROM publications WHERE id=?", (publication_id,)
            ).fetchone()
            if row is None:
                raise SwarmAccessError("Publication unavailable in this team")
            record = json.loads(row[0])
            if receipt["sha256"] != record["sha256"]:
                raise SwarmConflictError("Destination receipt hash does not match accepted output")
            if record["state"] == "published":
                if record["receipt"] != receipt:
                    raise SwarmConflictError("Published destination receipt is immutable")
                return record
            destination_row = connection.execute(
                "SELECT record FROM destinations WHERE id=?", (record["destination_id"],)
            ).fetchone()
            destination = json.loads(destination_row[0]) if destination_row else {}
            if (
                record["state"] != "pending"
                or not destination.get("active")
                or destination.get("version") != record["destination_version"]
            ):
                raise SwarmAccessError("Publication destination grant revoked or stale")
            record.update(state="published", receipt=receipt, published_at=store.clock())
            connection.execute(
                "UPDATE publications SET state='published',record=? WHERE id=?",
                (_json(record), publication_id),
            )
            store._event(
                connection,
                "publication.published",
                "Destination confirmed durable accepted output",
                data={"publication_id": publication_id},
            )
            return record

    def backup(self, destination: str | Path) -> dict[str, Any]:
        """Take one consistent SQLite snapshot with all referenced immutable objects.

        Backup targets are supplied by trusted owner control, never a worker path.
        Existing destinations are refused to preserve prior recovery evidence.
        """
        from contextlib import closing

        from jarvis.swarm.store import SCHEMA_VERSION, SwarmConflictError, _connection_permit, _json

        store = cast("TeamStore", self)
        target = Path(destination).resolve()
        if (
            target.exists()
            or target == store.path.parent.resolve()
            or store.path.parent.resolve() in target.parents
        ):
            raise SwarmConflictError("Backup requires a new directory outside this team workspace")
        target.mkdir(parents=True)
        # A write transaction prevents retention from deleting referenced objects
        # while a separate connection captures the committed source snapshot.
        with store._tx(write=True) as connection:
            team = store._team(connection)
            with (
                _connection_permit(),
                closing(sqlite3.connect(store.path)) as source,
                _connection_permit(),
                closing(sqlite3.connect(target / "team.sqlite3")) as backup_connection,
            ):
                source.backup(backup_connection, pages=128)
            objects = target / "objects"
            objects.mkdir()
            count = 0
            for row in connection.execute("SELECT id FROM artifacts"):
                record, _ = self._artifact_content(connection, row[0])
                shutil.copyfile(
                    store.path.parent / "objects" / record["object_key"],
                    objects / record["object_key"],
                )
                count += 1
            manifest = dict(
                team_id=store.team_id,
                schema_version=SCHEMA_VERSION,
                team_version=team["version"],
                artifacts=str(count),
                created_at=store.clock(),
            )
            (target / "manifest.json").write_text(_json(manifest), encoding="utf-8")
            return dict(manifest, path=str(target))

    def retain(self, controller: SwarmController, before: float) -> dict[str, str]:
        """Prune expired delivered message payloads; accepted/publication objects stay.

        Ordinary records, task evidence and published objects are never targets.
        """
        store = cast("TeamStore", self)
        with store._tx(write=True) as connection:
            store._controller(connection, controller)
            cursor = connection.execute(
                "DELETE FROM messages WHERE expires_at<? AND NOT EXISTS (SELECT 1 FROM "
                "deliveries d WHERE d.message_id=messages.id AND d.acknowledged_at IS NULL)",
                (min(before, store.clock()),),
            )
            return {"expired_messages": str(cursor.rowcount)}
