"""Task-Queue (Phase 5 Capability 4).

Persistente, crash-safe Tasks mit Retry-Logic. Storage in der Memory-DB
(ADR-0003), Scheduler ist Lightweight + asyncio (ADR-0005).

Exports:
- Schema-Klassen (TaskSpec, Trigger-Varianten, Action-Varianten) aus
  ``jarvis.tasks.schema``.
- Implementations: ``TaskStore``, ``TaskScheduler``, ``TaskRunner``.
"""
from __future__ import annotations

from .runner import TaskRunner
from .scheduler import TaskScheduler
from .schema import (
    TASK_STATES,
    TRIGGER_TYPES,
    HarnessDispatchAction,
    RetryPolicy,
    SpeakAction,
    TaskAction,
    TaskSpec,
    TaskState,
    ToolCallAction,
    Trigger,
    TriggerAfterDelay,
    TriggerAtTime,
    TriggerOnEvent,
)
from .store import TaskStore

__all__ = [
    # Schema
    "TaskSpec",
    "TaskAction",
    "HarnessDispatchAction",
    "SpeakAction",
    "ToolCallAction",
    "TriggerAfterDelay",
    "TriggerAtTime",
    "TriggerOnEvent",
    "Trigger",
    "RetryPolicy",
    "TaskState",
    "TRIGGER_TYPES",
    "TASK_STATES",
    # Implementation
    "TaskStore",
    "TaskScheduler",
    "TaskRunner",
]
