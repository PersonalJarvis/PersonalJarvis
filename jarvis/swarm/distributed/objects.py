"""Immutable, hash-checked objects in explicitly configured cloud S3/R2."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, SwarmStoreError

from .database import team_schema


class S3Objects:
    def __init__(self, config, secrets, *, client=None):
        self.config, self.secrets = config, secrets
        self._client = client
        self._lock = threading.Lock()

    def client(self):
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                self.secrets.validate()
                try:
                    import boto3
                    from botocore.config import Config
                except ImportError:
                    raise SwarmStoreError(
                        "Install optional swarm-distributed dependencies in the application"
                    ) from None
                self._client = boto3.session.Session().client(
                    "s3",
                    endpoint_url=self.config.s3_endpoint_url,
                    region_name=self.config.s3_region,
                    aws_access_key_id=self.secrets.s3_access_key_id,
                    aws_secret_access_key=self.secrets.s3_secret_access_key,
                    aws_session_token=self.secrets.s3_session_token or None,
                    config=Config(
                        signature_version="s3v4",
                        connect_timeout=5,
                        read_timeout=10,
                        max_pool_connections=self.config.max_connections,
                        retries={"mode": "standard", "total_max_attempts": 2},
                    ),
                )
            return self._client

    def key(self, team_id: str, digest: str) -> str:
        team_schema(self.config.namespace, team_id)
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise SwarmAccessError("Invalid team object hash")
        return f"{self.config.namespace}/{team_id}/objects/{digest}"

    def check_connection(self):
        try:
            self.client().head_bucket(Bucket=self.config.s3_bucket)
        except SwarmStoreError:
            raise
        except Exception:
            raise SwarmStoreError(
                "S3 bucket unavailable; verify its endpoint and credentials in settings"
            ) from None

    def put(self, team_id: str, digest: str, data: bytes):
        key = self.key(team_id, digest)
        if len(data) > 10_000_000 or hashlib.sha256(data).hexdigest() != digest:
            raise SwarmConflictError("Object bytes do not match their bounded content hash")
        try:
            result = self.client().put_object(
                Bucket=self.config.s3_bucket,
                Key=key,
                Body=data,
                ContentLength=len(data),
                IfNoneMatch="*",
                Metadata={"sha256": digest},
                ContentType="application/octet-stream",
            )
            return {"object_version_id": result.get("VersionId", "")}
        except SwarmStoreError:
            raise
        except Exception as error:
            response = getattr(error, "response", {})
            if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise SwarmStoreError(
                    "S3 object write failed; retry after verifying storage in settings"
                ) from None
        # Conditional write conflict means content may already exist. Read and
        # verify rather than trusting a user-editable metadata hash or an ETag.
        existing, version = self._read(key)
        if existing != data:
            raise SwarmConflictError("Existing S3 object failed immutable content validation")
        return {"object_version_id": version}

    def _read(self, key: str, version: str = "") -> tuple[bytes, str]:
        kwargs = dict(Bucket=self.config.s3_bucket, Key=key)
        if version and version != "null":
            kwargs["VersionId"] = version
        try:
            response = self.client().get_object(**kwargs)
            body = response["Body"]
            try:
                if int(response["ContentLength"]) > 10_000_000:
                    raise SwarmStoreError("S3 object exceeds its bounded artifact size")
                content = body.read(10_000_001)
                if len(content) > 10_000_000:
                    raise SwarmStoreError("S3 object exceeds its bounded artifact size")
            finally:
                body.close()
            return content, response.get("VersionId", "")
        except SwarmStoreError:
            raise
        except Exception:
            raise SwarmStoreError(
                "S3 object unavailable; verify storage or restore its backup"
            ) from None

    def read(self, team_id: str, record: dict) -> bytes:
        if record.get("team_id") != team_id:
            raise SwarmAccessError("Object record belongs to another team")
        key = self.key(team_id, record["object_key"])
        data, _ = self._read(key, record.get("object_version_id", ""))
        if (
            len(data) != int(record["size_bytes"])
            or hashlib.sha256(data).hexdigest() != record["sha256"]
        ):
            raise SwarmStoreError("S3 object failed size/hash verification; restore its backup")
        return data

    def put_file(self, team_id: str, digest: str, path: Path):
        """Stream a trusted restore file without loading it into application memory."""
        size = path.stat().st_size
        calculated = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(65536), b""):
                calculated.update(chunk)
        if size > 10_000_000 or calculated.hexdigest() != digest:
            raise SwarmConflictError("Restore object failed its bounded content validation")
        key = self.key(team_id, digest)
        try:
            with path.open("rb") as source:
                result = self.client().put_object(
                    Bucket=self.config.s3_bucket,
                    Key=key,
                    Body=source,
                    ContentLength=size,
                    IfNoneMatch="*",
                    Metadata={"sha256": digest},
                    ContentType="application/octet-stream",
                )
            return {"object_version_id": result.get("VersionId", "")}
        except Exception as error:
            response = getattr(error, "response", {})
            if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise SwarmStoreError(
                    "Restore object upload failed; retry after checking storage"
                ) from error
        response = self.client().get_object(Bucket=self.config.s3_bucket, Key=key)
        body = response["Body"]
        try:
            if int(response["ContentLength"]) != size:
                raise SwarmConflictError("Existing restore object length disagrees")
            calculated = hashlib.sha256()
            copied = 0
            for chunk in iter(lambda: body.read(65536), b""):
                copied += len(chunk)
                if copied > size:
                    raise SwarmConflictError("Existing restore object exceeds its verified length")
                calculated.update(chunk)
            if copied != size or calculated.hexdigest() != digest:
                raise SwarmConflictError("Existing restore object failed immutable validation")
            return {"object_version_id": response.get("VersionId", "")}
        finally:
            body.close()

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None
