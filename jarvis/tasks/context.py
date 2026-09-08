"""Per-turn scheduling context; never a process-global user's timezone."""

from contextvars import ContextVar

client_timezone: ContextVar[str | None] = ContextVar("routine_client_timezone", default=None)
