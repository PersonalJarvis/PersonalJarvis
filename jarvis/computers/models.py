"""Records for the Computers section.

One :class:`Computer` per machine. ``facts`` is what the machine IS (OS, cores,
memory) and changes rarely; ``health`` is how it is doing right now and is
rewritten by every check. Both are plain data — nothing here talks to a
network.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


#: Where an address came from: typed in, suggested by Tailscale, or read from
#: this PC's ``~/.ssh/config``.
RouteSource = Literal["manual", "tailscale", "ssh_config"]

#: The id of the route a record without routes is given on read.
PRIMARY_ROUTE_ID = "r_main"


class ComputerRoute(BaseModel):
    """One address the machine answers on (a LAN IP, a Tailscale name, …)."""

    model_config = ConfigDict(frozen=True)

    id: str
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    #: A short name the user gave this way in ("Home LAN", "Tailscale").
    label: str | None = None
    source: RouteSource = "manual"
    #: When a connection last got through on this address (epoch seconds).
    last_ok_at: float | None = None


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
    #: The log correlation id of a failed check, for a bug report.
    trace_id: str | None = None


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
    #: Switched off: kept with its login, but nothing connects to it, nothing
    #: is checked and no picker offers it until it is switched on again.
    enabled: bool = True
    #: Every address the machine answers on, preferred first. A connection
    #: tries them in this order; ``host``/``port`` mirror the one that last
    #: got through (``active_route_id``), so readers of a single address keep
    #: working. A record stored before routes existed gets its ``host`` as the
    #: only route.
    routes: list[ComputerRoute] = Field(default_factory=list)
    active_route_id: str | None = None
    #: How often "Automatic" sends a new workspace here: 0 never, 1 less,
    #: 2 normal, 3 more (see :mod:`jarvis.computers.placement`).
    placement_weight: int = Field(default=2, ge=0, le=3)
    #: When this PC's GitHub login was last shared with the agents here;
    #: ``None`` when it is not shared (see :mod:`jarvis.computers.github_access`).
    github_shared_at: float | None = None

    @model_validator(mode="after")
    def _routes_cover_the_address(self) -> Computer:
        if not self.routes:
            route = ComputerRoute(id=PRIMARY_ROUTE_ID, host=self.host, port=self.port)
            object.__setattr__(self, "routes", [route])
            object.__setattr__(self, "active_route_id", route.id)
        elif self.active_route_id not in {r.id for r in self.routes}:
            object.__setattr__(self, "active_route_id", self.routes[0].id)
        return self

    def route(self, route_id: str | None) -> ComputerRoute | None:
        return next((r for r in self.routes if r.id == route_id), None)
