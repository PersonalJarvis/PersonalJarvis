"""Trusted per-task target, recovered from the persisted scheduler assignment."""

from contextvars import ContextVar

target_machine: ContextVar[str | None] = ContextVar("machine_task_target", default=None)
