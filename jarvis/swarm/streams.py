"""Bounded owner-authorized object streams with deterministic handle cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import AsyncIterator
from contextlib import closing, suppress
from pathlib import Path
from typing import Any

from .store import SwarmAccessError, SwarmStoreError

CHUNK_BYTES = 65_536
log = logging.getLogger(__name__)


async def finish_cleanup(task: asyncio.Task[Any]) -> Any:
    """Keep ownership until a worker-thread operation actually finishes."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Repeated disconnect cancellation must not detach a live handle.
            continue
    return task.result()


async def acquire_stream(offload: Any, opener: Any, *args: Any) -> Any:
    opening = asyncio.create_task(offload(opener, *args))
    try:
        return await asyncio.shield(opening)
    except asyncio.CancelledError:
        try:
            stream = await finish_cleanup(opening)
        except Exception:
            log.debug("Canceled stream acquisition failed before creating a handle", exc_info=True)
        else:
            await finish_cleanup(asyncio.create_task(offload(stream.close)))
        raise


def artifact_record(store: Any, artifact_id: str) -> dict[str, Any]:
    with store._tx() as connection:
        store._team(connection)
        row = connection.execute(
            "SELECT record FROM artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
    if row is None:
        raise SwarmAccessError("Artifact unavailable in this team")
    record = json.loads(row[0])
    if record.get("team_id") != store.team_id or not 0 <= int(record["size_bytes"]) <= 10_000_000:
        raise SwarmAccessError("Artifact belongs to another team")
    return record


def open_artifact(store: Any, record: dict[str, Any]) -> Any:
    """Open a scoped local file or S3 body; never read its whole contents."""
    key = record["object_key"]
    if not re.fullmatch(r"[a-f0-9]{64}", key) or key != record["sha256"]:
        raise SwarmAccessError("Invalid content-addressed artifact")
    if hasattr(store, "registry") and hasattr(store.registry, "objects"):
        objects = store.registry.objects
        arguments = {"Bucket": objects.config.s3_bucket, "Key": objects.key(store.team_id, key)}
        version = record.get("object_version_id")
        if version and version != "null":
            arguments["VersionId"] = version
        try:
            response = objects.client().get_object(**arguments)
            body = response["Body"]
            if int(response["ContentLength"]) != int(record["size_bytes"]):
                body.close()
                raise SwarmStoreError("S3 artifact length disagrees with its verified record")
            return body
        except SwarmStoreError:
            raise
        except Exception as exc:
            raise SwarmStoreError(
                "Artifact storage is unavailable; retry or restore a backup"
            ) from exc
    directory = store.path.parent / "objects"
    path = directory / key
    if directory.is_symlink() or path.is_symlink() or path.resolve().parent != directory.resolve():
        raise SwarmAccessError("Artifact path escaped its team")
    if directory.resolve().parent != store.path.parent.resolve():
        raise SwarmAccessError("Artifact directory escaped its team")
    stream = path.open("rb")
    if path.stat().st_size != int(record["size_bytes"]):
        stream.close()
        raise SwarmStoreError("Artifact length disagrees with its verified record")
    return stream


def publication_record(
    path: Path, publication_id: str, owner: str = "local-user"
) -> dict[str, str]:
    if not re.fullmatch(r"[a-f0-9]{32}", publication_id) or not path.is_file():
        raise SwarmAccessError("Publication unavailable")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        row = connection.execute(
            "SELECT sha256,length(content) FROM publications WHERE id=? AND owner=?",
            (publication_id, owner),
        ).fetchone()
    if row is None or not 0 <= row[1] <= 50_000_000:
        raise SwarmAccessError("Publication unavailable")
    return {"sha256": row[0], "size_bytes": str(row[1])}


async def verified_chunks(
    stream: Any, record: dict[str, Any], offload: Any, *, authorize: Any = None
) -> AsyncIterator[bytes]:
    """Hold the final chunk until EOF verifies size/hash; cancellation closes I/O."""
    total = 0
    digest = hashlib.sha256()

    async def read_chunk() -> bytes:
        reading = asyncio.create_task(offload(stream.read, CHUNK_BYTES))
        try:
            return await asyncio.shield(reading)
        except asyncio.CancelledError:
            try:
                await finish_cleanup(reading)
            except Exception as exc:
                log.debug("Canceled stream read finished with %s", type(exc).__name__)
            raise

    try:
        current = await read_chunk()
        while current:
            if authorize is not None:
                await offload(authorize)
            total += len(current)
            if total > int(record["size_bytes"]):
                raise SwarmStoreError("Artifact exceeds its verified length")
            digest.update(current)
            following = await read_chunk()
            if not following and (
                total != int(record["size_bytes"]) or digest.hexdigest() != record["sha256"]
            ):
                raise SwarmStoreError("Artifact failed hash verification; restore its backup")
            yield current
            current = following
        if total != int(record["size_bytes"]) or digest.hexdigest() != record["sha256"]:
            raise SwarmStoreError("Artifact failed hash verification; restore its backup")
    finally:
        # The caller awaits this generator's close on disconnect. The stream
        # never holds an application DB transaction during client backpressure.
        await finish_cleanup(asyncio.create_task(offload(stream.close)))


class PublicationStream:
    def __init__(self, path: Path, publication_id: str, owner: str = "local-user"):
        if not re.fullmatch(r"[a-f0-9]{32}", publication_id) or not path.is_file():
            raise SwarmAccessError("Publication unavailable")
        self.connection = sqlite3.connect(
            path.as_uri() + "?mode=ro", uri=True, check_same_thread=False
        )
        try:
            row = self.connection.execute(
                "SELECT rowid,sha256,length(content) FROM publications WHERE id=? AND owner=?",
                (publication_id, owner),
            ).fetchone()
            if row is None:
                raise SwarmAccessError("Publication unavailable")
            self.record = {"sha256": row[1], "size_bytes": str(row[2])}
            self.blob = self.connection.blobopen("publications", "content", row[0], readonly=True)
        except BaseException:
            self.connection.close()
            raise

    def read(self, size: int) -> bytes:
        return self.blob.read(size)

    def close(self) -> None:
        with suppress(sqlite3.Error):
            self.blob.close()
        self.connection.close()
