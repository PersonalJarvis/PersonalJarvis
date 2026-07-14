"""Lazy public exports for the brain layer.

Configuration imports the lightweight ``brain.ack_brain.config`` module while
the safety layer may still be initializing. Eagerly importing the dispatcher,
manager, and tool loop here creates a safety/config/brain cycle, so public
objects are resolved only when callers request them.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "BrainDispatcher",
    "BrainManager",
    "BrainProviderRegistry",
    "CacheHeartbeat",
    "IterationBudget",
    "StreamingAggregate",
    "ToolUseLoop",
    "aggregate",
    "tee_text",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "BrainDispatcher": (".dispatcher", "BrainDispatcher"),
    "BrainManager": (".manager", "BrainManager"),
    "BrainProviderRegistry": (".provider_registry", "BrainProviderRegistry"),
    "CacheHeartbeat": (".cache_heartbeat", "CacheHeartbeat"),
    "IterationBudget": (".iteration_budget", "IterationBudget"),
    "StreamingAggregate": (".streaming", "StreamingAggregate"),
    "ToolUseLoop": (".tool_use_loop", "ToolUseLoop"),
    "aggregate": (".streaming", "aggregate"),
    "tee_text": (".streaming", "tee_text"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
