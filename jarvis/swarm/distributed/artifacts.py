"""Remote object I/O outside authority locks, with durable quota reservations."""

from __future__ import annotations

import copy
import hashlib
import json
from contextlib import nullcontext

from jarvis.swarm.objects import ObjectMixin
from jarvis.swarm.store import (
    SwarmAccessError,
    SwarmBudgetError,
    SwarmConflictError,
    _digest,
    _json,
    _page,
)


def _upload_fingerprint(task_id, fence, name, digest, media_type, origin, receipt=None):
    from jarvis.swarm.receipts import canonical

    return _digest(canonical([task_id, fence, name, digest, media_type, origin, receipt]))


def _recovered_receipt(original, data, team_id):
    """Recover old receipt authority only if the durable reservation attests it."""
    from jarvis.swarm.receipts import provenance

    try:
        receipt = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        # Ordinary artifact bytes need not be JSON and confer no receipt authority.
        return None
    if not isinstance(receipt, dict) or receipt.get("origin") != "runtime-receipt":
        return None
    fingerprint = _upload_fingerprint(
        original["task_id"],
        original["task_fence"],
        original["name"],
        original["sha256"],
        original["media_type"],
        "runtime-receipt",
        receipt,
    )
    if (
        fingerprint != original["fingerprint"]
        or receipt.get("team_id") != team_id
        or receipt.get("source_agent_id") != original["owner_id"]
        or receipt.get("source_task_id") != original["task_id"]
        or receipt.get("source_attempt") != original["task_fence"]
    ):
        return None
    return provenance(receipt)


class RemoteArtifacts:
    """Unknown uploads retain quota until an idempotent retry or team deletion."""

    def _bound(self, connection):
        # This short-lived trusted facade keeps shared authorization/ledger
        # writes in the caller's already-open PostgreSQL transaction.
        bound = copy.copy(self)
        bound._tx = lambda *, write=False: nullcontext(connection)
        return bound

    def _write_artifact(
        self,
        actor,
        name,
        content,
        request_key,
        *,
        media_type="text/plain",
        expected_version=None,
        controller=None,
        receipt=None,
    ):
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
        upload_id = _digest(actor.agent_id + ":" + request_key)
        origin = "worker-authored" if controller is None else "worker-output"
        if receipt is not None:
            from jarvis.swarm.receipts import build_receipt, canonical

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
            origin = "runtime-receipt"
        fingerprint = _upload_fingerprint(
            actor.task_id, actor.task_fence, name, digest, media_type, origin, receipt
        )
        with self._tx(write=True) as connection:
            self._actor(connection, actor, writing=True)
            if controller is None:
                self._require_tool(connection, "write_artifact")
            else:
                self._controller(connection, controller)
            if not actor.task_id:
                raise SwarmAccessError("Artifacts require an active task attempt")
            if receipt is not None:
                self._receipt_subjects(connection, actor, receipt)
            existing = connection.execute(
                "SELECT 1 FROM artifacts WHERE owner_id=? AND request_key=?",
                (actor.agent_id, request_key),
            ).fetchone()
            if existing:
                return ObjectMixin._write_artifact(
                    self._bound(connection),
                    actor,
                    name,
                    data,
                    request_key,
                    media_type=media_type,
                    expected_version=expected_version,
                    controller=controller,
                    receipt=receipt,
                )
            upload = connection.execute(
                "SELECT fingerprint FROM object_uploads WHERE id=?", (upload_id,)
            ).fetchone()
            if upload:
                legacy = _digest(_json([actor.task_id, actor.task_fence, name, digest, media_type]))
                if upload[0] != fingerprint and (receipt is not None or upload[0] != legacy):
                    raise SwarmConflictError(
                        "Object upload key identifies different content or attempt"
                    )
            else:
                row = connection.execute(
                    "SELECT value FROM counters WHERE name='artifact_bytes'"
                ).fetchone()
                used = int(row[0]) if row else 0
                pending = int(
                    connection.execute(
                        "SELECT coalesce(sum(size_bytes),0) FROM object_uploads "
                        "WHERE state<>'completed'"
                    ).fetchone()[0]
                )
                if used + pending + len(data) > int(
                    self._team(connection)["policy"]["max_artifact_bytes"]
                ):
                    raise SwarmBudgetError(
                        "Team artifact quota includes pending or unknown uploads"
                    )
                connection.execute(
                    "INSERT INTO object_uploads (id,owner_id,task_id,fingerprint,size_bytes,state,"
                    "created_at,task_fence,sha256,name,media_type,request_key) "
                    "VALUES (?,?,?,?,?,'reserved',?,?,?,?,?,?)",
                    (
                        upload_id,
                        actor.agent_id,
                        actor.task_id,
                        fingerprint,
                        len(data),
                        self.clock(),
                        actor.task_fence,
                        digest,
                        name,
                        media_type,
                        request_key,
                    ),
                )
        # Neither a slow S3 request nor a broken endpoint owns the team write lock.
        try:
            metadata = self.registry.objects.put(self.team_id, digest, data)
        except Exception:
            with self._tx(write=True) as connection:
                connection.execute(
                    "UPDATE object_uploads SET state='unknown' WHERE id=? AND state<>'completed'",
                    (upload_id,),
                )
            raise
        with self._tx(write=True) as connection:
            bound = self._bound(connection)
            bound._put_object = lambda _data, _digest: metadata
            # Shared code rechecks membership, controller/attempt fencing,
            # lifecycle, immutable operation key and quota immediately before commit.
            record = ObjectMixin._write_artifact(
                bound,
                actor,
                name,
                data,
                request_key,
                media_type=media_type,
                expected_version=expected_version,
                controller=controller,
                receipt=receipt,
            )
            connection.execute(
                "UPDATE object_uploads SET state='completed' WHERE id=?", (upload_id,)
            )
            return record

    def pending_uploads(self, controller, actor, *, limit=16):
        """List older-attempt reservations for this currently authorized task."""
        _page(limit)
        with self._tx() as connection:
            self._controller(connection, controller)
            self._actor(connection, actor, writing=True)
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM object_uploads WHERE task_id=? AND task_fence<? "
                    "AND state<>'completed' ORDER BY created_at,id LIMIT ?",
                    (actor.task_id, actor.task_fence, limit),
                )
            ]

    def recover_upload(self, controller, actor, upload_id, content=None):
        """Transfer verified same-task bytes to a replacement without double charge.

        If an unknown write never reached S3, a replacement can supply bytes
        matching the saved hash. Recovery never refunds unverified exposure and
        never marks a task accepted; the ordinary verifier still decides that.
        """
        with self._tx() as connection:
            self._controller(connection, controller)
            self._actor(connection, actor, writing=True)
            row = connection.execute(
                "SELECT * FROM object_uploads WHERE id=?", (upload_id,)
            ).fetchone()
            if (
                row is None
                or row["task_id"] != actor.task_id
                or row["task_fence"] >= actor.task_fence
            ):
                raise SwarmAccessError(
                    "Only a fenced earlier attempt of this task can be recovered"
                )
            original = dict(row)
            if original["recovered_artifact_id"]:
                artifact = connection.execute(
                    "SELECT record FROM artifacts WHERE id=?", (original["recovered_artifact_id"],)
                ).fetchone()
                if artifact is None:
                    raise SwarmAccessError("Recovered artifact is unavailable")
                record = json.loads(artifact[0])
                if (
                    record["owner_id"] != actor.agent_id
                    or record["attempt_fence"] != actor.task_fence
                ):
                    raise SwarmConflictError("Upload was already transferred to another attempt")
                return record
            if original["state"] == "completed":
                raise SwarmConflictError("This upload already has a committed artifact")
        if content is None:
            data = self.registry.objects.read(
                self.team_id,
                dict(
                    team_id=self.team_id,
                    object_key=original["sha256"],
                    sha256=original["sha256"],
                    size_bytes=str(original["size_bytes"]),
                ),
            )
        else:
            data = content.encode("utf-8") if isinstance(content, str) else content
        if (
            len(data) != original["size_bytes"]
            or hashlib.sha256(data).hexdigest() != original["sha256"]
        ):
            raise SwarmConflictError("Recovery bytes do not match the original reserved object")
        metadata = self.registry.objects.put(self.team_id, original["sha256"], data)
        metadata["recovered_from"] = dict(
            upload_id=upload_id,
            source_agent_id=original["owner_id"],
            source_task_id=original["task_id"],
            source_attempt=original["task_fence"],
        )
        receipt_source = _recovered_receipt(original, data, self.team_id)
        if receipt_source is not None:
            # Preserve the original authority for audit. The replacement's
            # artifact remains worker-output, never a new observation or verdict.
            metadata["recovered_from"]["provenance"] = receipt_source
        with self._tx(write=True) as connection:
            self._controller(connection, controller)
            self._actor(connection, actor, writing=True)
            current = connection.execute(
                "SELECT * FROM object_uploads WHERE id=?", (upload_id,)
            ).fetchone()
            if current is None or current["fingerprint"] != original["fingerprint"]:
                raise SwarmConflictError("Upload reservation changed during recovery")
            if current["state"] == "completed" and not current["recovered_artifact_id"]:
                raise SwarmConflictError("Original upload completed before recovery")
            bound = self._bound(connection)
            bound._put_object = lambda _data, _digest: metadata
            record = ObjectMixin._write_artifact(
                bound,
                actor,
                original["name"],
                data,
                original["request_key"],
                media_type=original["media_type"],
                controller=controller,
            )
            connection.execute(
                "UPDATE object_uploads SET state='completed',recovered_artifact_id=? WHERE id=?",
                (record["id"], upload_id),
            )
            return record

    def queue_publication(
        self, controller, lead_actor, artifact_id, destination_id, request_key, expected_version=1
    ):
        with self._tx() as connection:
            self._controller(connection, controller)
            if connection.execute(
                "SELECT 1 FROM publications WHERE request_key=?", (request_key,)
            ).fetchone():
                return ObjectMixin.queue_publication(
                    self._bound(connection),
                    controller,
                    lead_actor,
                    artifact_id,
                    destination_id,
                    request_key,
                    expected_version,
                )
            previous, data = self._artifact_content(connection, artifact_id)
        with self._tx(write=True) as connection:
            bound = self._bound(connection)

            def verified_bytes(record):
                if record != previous:
                    raise SwarmConflictError("Publication artifact changed during verification")
                return data

            bound._read_object = verified_bytes
            return ObjectMixin.queue_publication(
                bound,
                controller,
                lead_actor,
                artifact_id,
                destination_id,
                request_key,
                expected_version,
            )
