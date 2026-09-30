"""Per-turn scheduling context; never a server-derived default timezone.

``client_timezone`` is the zone of the client that sent the current turn (the
agent chat sends it with every message). A voice turn has no client of its
own, so it falls back to the zone the person's desktop or web UI last
reported for this app — still the person's device, never the server clock.
With neither known, a calendar routine asks for the zone instead of guessing.
"""

from contextvars import ContextVar

client_timezone: ContextVar[str | None] = ContextVar("routine_client_timezone", default=None)

#: Header an in-process app command carries the turn's zone in.
CLIENT_TIMEZONE_HEADER = "X-Jarvis-Client-Timezone"

_reported_ui_timezone: str | None = None


def remember_ui_timezone(zone: str) -> None:
    """Record the IANA zone the person's UI reported (validated by the caller)."""
    global _reported_ui_timezone
    _reported_ui_timezone = zone


def turn_timezone() -> str | None:
    """The current turn's client zone, else the zone the UI last reported."""
    return client_timezone.get() or _reported_ui_timezone
