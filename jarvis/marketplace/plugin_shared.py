"""Shared handle to the active PluginToolRegistry (mirror of jarvis.clis.shared).

The UI server constructs the registry, bootstraps it, and publishes it here so
the brain loader and the marketplace routes see the SAME instance on the SAME
bus — no split-brain registry.
"""
from __future__ import annotations

from typing import Any

_active_registry: Any = None


def set_active_plugin_registry(registry: Any) -> None:
    global _active_registry
    _active_registry = registry


def get_active_plugin_registry() -> Any:
    return _active_registry
