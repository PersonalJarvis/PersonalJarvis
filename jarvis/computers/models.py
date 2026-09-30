"""Records for the Computers section.

One :class:`Computer` per machine. ``facts`` is what the machine IS (OS, cores,
memory) and changes rarely; ``health`` is how it is doing right now and is
rewritten by every check. Both are plain data — nothing here talks to a
network.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: A rented or self-hosted machine reached over the network, or a VM on this box.
ComputerKind = Literal["server", "local_vm"]

#: Where the machine came from: an id from :mod:`jarvis.computers.providers`
#: (``generic`` for any SSH host typed in by hand, ``multipass`` for a local
#: VM). A plain string so the catalog can grow without a schema change; the
#: service validates it against the catalog on every write.
ProviderId = str

#: How Jarvis logs in. ``key`` is Jarvis's own key pair (the default and the
#: recommended path); ``password`` keeps a password in the OS keyring;
#: ``private_key`` is the user's own SSH key, kept in the OS keyring.
AuthMethod = Literal["key", "password", "private_key"]
#: How a form asks to log in. ``auto`` is not stored: it tries the app's key
#: and this PC's own SSH keys, then plants the app's key (auth becomes "key").
LoginMode = Literal["key", "password", "private_key", "auto"]

#: The state a check leaves behind. ``provisioning`` belongs to a local VM that
#: is still being created; ``stopped`` to a local VM that is powered off.
HealthStatus = Literal[
    "unknown",
    "online",
    "offline",
    "auth_failed",
    "host_key_changed",
    "provisioning",
    "stopped",
    "error",
]


class ComputerFacts(BaseModel):
    """What the machine is, read by the last successful check."""

    model_config = ConfigDict(frozen=True)

    hostname: str | None = None
    os_id: str | None = None
    os_name: str | None = None
    kernel: str | None = None
    arch: str | None = None
    cpu_count: int | None = None
    mem_total_mb: int | None = None
    disk_total_gb: float | None = None


class ComputerHealth(BaseModel):
    """How the machine is doing, as of ``checked_at`` (epoch seconds)."""

    model_config = ConfigDict(frozen=True)

    status: HealthStatus = "unknown"
    checked_at: float | None = None
    latency_ms: int | None = None
    #: A sentence for the user when the status is not ``online``.
    message: str | None = None
    load_1m: float | None = None
    mem_used_pct: float | None = None
    disk_used_pct: float | None = None
    uptime_s: int | None = None


class Computer(BaseModel):
    """One machine the user connected."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    kind: ComputerKind = "server"
    provider: ProviderId = "generic"
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    username: str = "root"
    auth: AuthMethod = "key"
    #: The machine's id at its provider (Hostinger VM id, droplet id, the
    #: Multipass instance name). ``None`` for a hand-typed server.
    provider_ref: str | None = None
    region: str | None = None
    plan: str | None = None
    #: The server's own SSH host key (OpenSSH line), pinned on first contact.
    #: A later mismatch refuses to connect instead of trusting a stranger.
    host_key: str | None = None
    host_fingerprint: str | None = None
    created_at: float
    facts: ComputerFacts | None = None
    health: ComputerHealth = Field(default_factory=ComputerHealth)
