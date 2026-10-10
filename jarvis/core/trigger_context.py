"""Trusted execution ancestry, separate from externally supplied payloads."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

current_trigger_path: ContextVar[tuple[str, ...]] = ContextVar("current_trigger_path", default=())

#: True while work the person started THEMSELVES runs — "Run now" on a task,
#: routine or workflow. The default (False) means unattended: a schedule, a
#: trigger, a webhook or any other background start. asyncio tasks copy the
#: context when they are created, so a run spawned inside the block keeps it.
current_started_by_user: ContextVar[bool] = ContextVar("current_started_by_user", default=False)


@contextmanager
def started_by_user() -> Iterator[None]:
    """Mark the work started inside this block as started by the person."""
    token = current_started_by_user.set(True)
    try:
        yield
    finally:
        current_started_by_user.reset(token)


class CapacityDeferred(RuntimeError):
    """Unattended work found nothing it may bill and was skipped, not run."""


class RoutineDeferred(RuntimeError):
    """Admission succeeded, but the owning agent has not started any side effect."""
