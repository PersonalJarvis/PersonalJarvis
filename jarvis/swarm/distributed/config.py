"""Credential-free settings and separately resolved control-plane secrets."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        # A DNS hostname is not a literal loopback address and must not bypass TLS.
        return False


class DistributedConfig(BaseModel):
    """Only user-selected endpoints; optional drivers are loaded on first use."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    postgres_dsn: str = Field(min_length=1, max_length=2048)
    redis_url: str = Field(min_length=1, max_length=2048)
    s3_endpoint_url: str = Field(min_length=1, max_length=2048)
    s3_region: str = Field(default="auto", pattern=r"^[a-zA-Z0-9-]{1,50}$")
    s3_bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    namespace: str = Field(default="jarvis_swarm", pattern=r"^[a-z][a-z0-9_]{0,19}$")
    max_connections: int = Field(default=16, ge=2, le=64)
    # Only explicit disposable test/local services may disable transport TLS.
    allow_insecure_localhost: bool = False

    @model_validator(mode="after")
    def endpoints(self):
        for label, endpoint, schemes in (
            ("PostgreSQL", self.postgres_dsn, {"postgresql", "postgres"}),
            ("Redis", self.redis_url, {"rediss", "redis"}),
            ("S3", self.s3_endpoint_url, {"https", "http"}),
        ):
            parsed = urlsplit(endpoint)
            if parsed.scheme not in schemes or not parsed.hostname or parsed.fragment:
                raise ValueError(f"Invalid {label} endpoint")
            if parsed.password is not None:
                raise ValueError(f"Resolve {label} credentials separately from endpoint settings")
            query = parse_qs(parsed.query)
            if any(key not in {"sslmode"} for key in query):
                raise ValueError(f"Unsupported {label} endpoint query settings")
            insecure = self.allow_insecure_localhost and _loopback(parsed.hostname)
            if label == "PostgreSQL":
                if not parsed.username or not parsed.path.strip("/"):
                    raise ValueError("PostgreSQL endpoint requires a database and role")
                if not insecure and query.get("sslmode", ["verify-full"]) != ["verify-full"]:
                    raise ValueError(
                        "Remote PostgreSQL requires certificate and hostname verification"
                    )
            elif label == "Redis":
                if parsed.query or (parsed.scheme != "rediss" and not insecure):
                    raise ValueError("Remote Redis requires TLS with certificate verification")
                if parsed.path and not parsed.path.strip("/").isdigit():
                    raise ValueError("Redis database selector must be an integer")
            else:
                if parsed.username or parsed.query or parsed.path not in {"", "/"}:
                    raise ValueError("S3 endpoint must be an origin without credentials or a path")
                if parsed.scheme != "https" and not insecure:
                    raise ValueError("Remote S3 requires HTTPS")
        return self


@dataclass(frozen=True)
class DistributedSecrets:
    """Resolve these through get_secret in the authenticated configuration layer."""

    postgres_password: str = field(repr=False)
    redis_password: str = field(repr=False)
    s3_access_key_id: str = field(repr=False)
    s3_secret_access_key: str = field(repr=False)
    s3_session_token: str = field(default="", repr=False)

    def validate(self) -> None:
        if not all(
            (
                self.postgres_password,
                self.redis_password,
                self.s3_access_key_id,
                self.s3_secret_access_key,
            )
        ):
            raise ValueError("Configure all distributed credentials in the application")
