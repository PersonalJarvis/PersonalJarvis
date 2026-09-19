"""Optional distributed setup: versioned secret references and atomic public settings."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr

log = logging.getLogger(__name__)
_SECRETS = ("postgres_password", "redis_password", "s3_access_key_id", "s3_secret_access_key")


class DistributedSetup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    postgres_dsn: SecretStr | None = None
    redis_url: SecretStr | None = None
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_endpoint: str | None = Field(default=None, max_length=2048)
    s3_region: str | None = Field(default=None, max_length=50)
    s3_bucket: str | None = Field(default=None, max_length=63)
    max_concurrency: int | None = Field(default=None, ge=1, le=10000)


def _split_credential(endpoint: str) -> tuple[str, str | None]:
    parsed = urlsplit(endpoint)
    if not parsed.hostname or parsed.fragment:
        raise ValueError("A valid database endpoint is required")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    if parsed.port is not None:
        host += f":{parsed.port}"
    if parsed.username is not None:
        host = quote(unquote(parsed.username), safe="") + "@" + host
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, "")), (
        unquote(parsed.password) if parsed.password is not None else None
    )


class DistributedSettings:
    def __init__(
        self,
        root: Path,
        *,
        read_secret: Callable[[str], str | None] | None = None,
        write_secret: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.path = root / "distributed-settings.sqlite3"
        if read_secret is None or write_secret is None:
            from jarvis.core.config import get_secret, set_secret

            read_secret = read_secret or get_secret
            write_secret = write_secret or set_secret
        self.read_secret = read_secret
        self.write_secret = write_secret

    def load(self) -> dict[str, Any]:
        defaults = {
            "enabled": False,
            "postgres_dsn": "",
            "redis_url": "",
            "s3_endpoint": "",
            "s3_region": "auto",
            "s3_bucket": "",
            "max_concurrency": 1000,
            "generation": "",
        }
        if not self.path.is_file():
            return defaults
        with sqlite3.connect(self.path, timeout=10) as connection:
            record = connection.execute("SELECT record FROM settings WHERE id=1").fetchone()
        return dict(defaults, **json.loads(record[0])) if record else defaults

    def _secret(self, record: dict[str, Any], key: str) -> str:
        generation = record.get("generation")
        return (self.read_secret(f"swarm_{generation}_{key}") or "") if generation else ""

    def resolved(self) -> tuple[Any, Any]:
        from .distributed import DistributedConfig, DistributedSecrets

        record = self.load()
        if not record["enabled"]:
            raise ValueError("Distributed mode is disabled")
        config = DistributedConfig(
            postgres_dsn=record["postgres_dsn"],
            redis_url=record["redis_url"],
            s3_endpoint_url=record["s3_endpoint"],
            s3_region=record["s3_region"],
            s3_bucket=record["s3_bucket"],
            max_connections=16,
        )
        secrets = DistributedSecrets(**{key: self._secret(record, key) for key in _SECRETS})
        secrets.validate()
        return config, secrets

    def public(self) -> dict[str, Any]:
        record = self.load()
        return {
            "enabled": record["enabled"],
            "postgres_configured": bool(record["postgres_dsn"]),
            "redis_configured": bool(record["redis_url"]),
            "object_credentials_configured": bool(
                self._secret(record, "s3_access_key_id")
                and self._secret(record, "s3_secret_access_key")
            ),
            "s3_endpoint": record["s3_endpoint"],
            "s3_region": record["s3_region"],
            "s3_bucket": record["s3_bucket"],
            "max_concurrency": record["max_concurrency"],
            "available": False,
            "reason": "Connect the optional services to enable distributed execution",
        }

    def save(self, body: DistributedSetup) -> dict[str, Any]:
        previous = self.load()
        record = dict(previous)
        secret_values = {key: self._secret(previous, key) for key in _SECRETS}
        for key in ("enabled", "s3_endpoint", "s3_region", "s3_bucket", "max_concurrency"):
            value = getattr(body, key)
            if value is not None:
                record[key] = value
        for key, secret_key in (
            ("postgres_dsn", "postgres_password"),
            ("redis_url", "redis_password"),
        ):
            wrapped = getattr(body, key)
            if wrapped is not None and wrapped.get_secret_value():
                endpoint, password = _split_credential(wrapped.get_secret_value())
                record[key] = endpoint
                if password is not None:
                    secret_values[secret_key] = password
        for key in ("s3_access_key_id", "s3_secret_access_key"):
            wrapped = getattr(body, key)
            if wrapped is not None and wrapped.get_secret_value():
                secret_values[key] = wrapped.get_secret_value()
        from .distributed import DistributedConfig

        # Drafts may be incomplete, but an unenabled draft is not a place to
        # persist credentials embedded in an endpoint or to bypass TLS policy.
        try:
            DistributedConfig(
                postgres_dsn=record["postgres_dsn"] or "postgresql://swarm@database.invalid/swarm",
                redis_url=record["redis_url"] or "rediss://cache.invalid/0",
                s3_endpoint_url=record["s3_endpoint"] or "https://objects.invalid",
                s3_region=record["s3_region"],
                s3_bucket=record["s3_bucket"] or "swarm-draft",
            )
        except ValueError as exc:
            raise ValueError("Use valid TLS endpoints and dedicated credential fields") from exc
        if record["enabled"]:
            from .distributed import DistributedSecrets

            DistributedConfig(
                postgres_dsn=record["postgres_dsn"],
                redis_url=record["redis_url"],
                s3_endpoint_url=record["s3_endpoint"],
                s3_region=record["s3_region"],
                s3_bucket=record["s3_bucket"],
            )
            DistributedSecrets(**secret_values).validate()
        generation = uuid4().hex
        for key, value in secret_values.items():
            if value and not self.write_secret(f"swarm_{generation}_{key}", value):
                raise RuntimeError(
                    "Credentials could not be saved; the previous setup remains active"
                )
        # Switch all references together only after every credential is durable.
        record["generation"] = generation
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=10) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, record TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO settings VALUES (1,?) "
                "ON CONFLICT(id) DO UPDATE SET record=excluded.record",
                (json.dumps(record),),
            )
        return self.public()
