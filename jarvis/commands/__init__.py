"""Command Registry package — one machine-readable catalog of app commands."""
from jarvis.commands.registry import (
    AppCommand,
    get_command,
    get_registry,
    registry_as_dicts,
)

__all__ = ["AppCommand", "get_command", "get_registry", "registry_as_dicts"]
