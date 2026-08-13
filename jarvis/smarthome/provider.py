"""The contract every smart-home platform implements, and how it reports health.

A provider is the ONLY place that knows a vendor's wire format. It answers four
questions and nothing else: are you connected, which devices exist, what is one
device doing right now, and please do this. Everything a provider returns is
already in the vocabulary of :mod:`jarvis.smarthome.models`.

Deliberately a ``Protocol`` rather than a base class, matching the rest of the
codebase (``jarvis.core.protocols``): a provider is a plain object that happens
to have the right methods, so a test double is a twenty-line class and not a
subclass carrying a framework with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from jarvis.smarthome.models import Command, CommandResult, Device, Room


class ConnectionState(StrEnum):
    """Where a provider stands, from the user's point of view."""

    #: Working. Devices are reachable right now.
    CONNECTED = "connected"
    #: Never set up on this machine.
    NOT_CONFIGURED = "not_configured"
    #: Credentials exist but the platform refused them — needs a reconnect.
    NEEDS_REAUTH = "needs_reauth"
    #: Credentials are fine, the platform is not answering (server down, VPN,
    #: laptop on a different network). Distinct from NEEDS_REAUTH on purpose:
    #: telling someone to re-authorize when their hub is simply off sends them
    #: down the wrong path entirely.
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """One provider's health, shaped for direct display."""

    provider: str
    display_name: str
    state: ConnectionState
    #: Plain-language sentence for the card. Never a raw exception string — a
    #: vendor error body can echo a token back at us (see token_store's same
    #: rule), so provider text never reaches storage or the screen verbatim.
    detail: str | None = None
    device_count: int | None = None
    #: Mirrors the marketplace ``Longevity`` wording so the two surfaces cannot
    #: disagree about how durable a connection is.
    longevity: str = "permanent"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "state": str(self.state),
            "detail": self.detail,
            "device_count": self.device_count,
            "longevity": self.longevity,
            **({"extra": dict(self.extra)} if self.extra else {}),
        }


@runtime_checkable
class SmartHomeProvider(Protocol):
    """One ecosystem, translated into the unified vocabulary."""

    #: Stable id, also the namespace prefix of every device id it emits.
    id: str
    display_name: str

    async def status(self) -> ProviderStatus:
        """Health check. MUST NOT raise — an unreachable hub is a *state*."""
        ...

    async def devices(self) -> list[Device]:
        """Every device this provider can see. Empty list when not connected."""
        ...

    async def rooms(self) -> list[Room]:
        """Rooms/areas as the platform defines them. May be empty."""
        ...

    async def execute(self, native_id: str, command: Command) -> CommandResult:
        """Run one command. Returns a failed result rather than raising."""
        ...
