"""Task queue (Phase 5 Capability 4).

Persistent, crash-safe tasks with retry logic. Storage in the memory DB
(ADR-0003), scheduler is lightweight + asyncio (ADR-0005).

Exports:
- Schema classes (TaskSpec, trigger variants, action variants) from
  ``jarvis.tasks.schema``.
- Implementations: ``TaskStore``, ``TaskScheduler``, ``TaskRunner``.
"""
from __future__ import annotations

from .runner import TaskRunner
from .scheduler import TaskScheduler
from .schema import (
    ACTION_KINDS,
    PLUGIN_SCOPES,
    TASK_STATES,
    TRIGGER_TYPES,
    AgentAction,
    HarnessDispatchAction,
    PluginGrant,
    PluginScope,
    RetryPolicy,
    SpeakAction,
    TaskAction,
    TaskSpec,
    TaskState,
    ToolCallAction,
    Trigger,
    TriggerAfterDelay,
    TriggerAtTime,
    TriggerEvery,
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
    "AgentAction",
    "PluginGrant",
    "PluginScope",
    "TriggerAfterDelay",
    "TriggerAtTime",
    "TriggerOnEvent",
    "TriggerEvery",
    "Trigger",
    "RetryPolicy",
    "TaskState",
    "TRIGGER_TYPES",
    "TASK_STATES",
    "ACTION_KINDS",
    "PLUGIN_SCOPES",
    # Implementation
    "TaskStore",
    "TaskScheduler",
    "TaskRunner",
]
