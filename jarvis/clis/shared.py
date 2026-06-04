"""Stub-Shared-State — Original-Modul speicherte die aktive CliToolRegistry."""
from __future__ import annotations

from typing import Any

_active_registry: Any = None


def set_active_registry(registry: Any) -> None:
    global _active_registry
    _active_registry = registry


def get_active_registry() -> Any:
    return _active_registry
