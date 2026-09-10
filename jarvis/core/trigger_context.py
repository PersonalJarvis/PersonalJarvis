"""Trusted execution ancestry, separate from externally supplied payloads."""

from contextvars import ContextVar

current_trigger_path: ContextVar[tuple[str, ...]] = ContextVar("current_trigger_path", default=())


class RoutineDeferred(RuntimeError):
    """Admission succeeded, but the owning agent has not started any side effect."""
