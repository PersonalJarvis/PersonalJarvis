"""How an agent's model call differs from a voice turn, for the brain plugins.

The plugins were tuned for the voice path: a 30 s read timeout so a hung
provider fails over fast, a temperature on every request, no prompt caching
unless a global switch is on. A Hermes / OpenClaw agent calling through
Jarvis' model gateway needs the opposite: a reasoning model may think for
minutes before its first token and a local server may prefill for longer,
the runtime decides sampling itself, and a tool loop resends the same long
prefix every round.

The gateway sets :data:`PROFILE` in the task that runs the plugin; every
other caller leaves it unset, so the voice path sends exactly what it did.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentRequestProfile:
    """One agent call's needs (``None`` fields keep the plugin's default)."""

    #: Seconds a stream may stay silent before the call fails.
    read_timeout_s: float | None = None
    #: Mark the request's stable prefix for the provider's prompt cache.
    prompt_cache: bool = False
    #: The caller chose no temperature: send none, the model's default applies.
    omit_temperature: bool = False


PROFILE: ContextVar[AgentRequestProfile | None] = ContextVar(
    "jarvis_agent_request_profile", default=None
)


def current() -> AgentRequestProfile | None:
    return PROFILE.get()


def http_timeout(*, connect: float = 5.0) -> Any:
    """An ``httpx.Timeout`` for the active profile, or ``None`` without one."""
    profile = PROFILE.get()
    if profile is None or profile.read_timeout_s is None:
        return None
    import httpx

    return httpx.Timeout(connect=connect, read=profile.read_timeout_s, write=60.0, pool=30.0)


def sends_temperature() -> bool:
    """Whether the request should carry a temperature."""
    profile = PROFILE.get()
    return not (profile is not None and profile.omit_temperature)
