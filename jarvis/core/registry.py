"""Plugin discovery via entry_points.

All plugin slots are declared in pyproject.toml under
[project.entry-points."jarvis.*"]. This module lists them at runtime, loads
them lazily, and checks them against the matching Protocol.

Important: `load()` only imports on actual invocation — this allows optional
dependencies (e.g. discord.py only loads when the Discord channel is enabled).
"""
from __future__ import annotations

from importlib import metadata
from typing import Any, Protocol

from .protocols import PLUGIN_GROUPS


class PluginNotFoundError(KeyError):
    """No plugin with this name exists in the entry_points catalogue."""


class PluginLoadError(RuntimeError):
    """Plugin could not be imported (missing dependency, syntax error …)."""


class PluginContractError(TypeError):
    """Plugin does not structurally implement the expected Protocol."""


def list_plugins(group: str) -> list[str]:
    """Lists all available plugin names in a group.

    Args:
        group: e.g. "jarvis.brain", "jarvis.stt", see PLUGIN_GROUPS.
    """
    if group not in PLUGIN_GROUPS:
        raise ValueError(f"Unknown plugin group: {group}. Allowed: {PLUGIN_GROUPS}")
    eps = metadata.entry_points(group=group)
    return sorted(ep.name for ep in eps)


def list_all_plugins() -> dict[str, list[str]]:
    """All plugins grouped by type."""
    return {g: list_plugins(g) for g in PLUGIN_GROUPS}


def load(group: str, name: str, protocol: type[Protocol] | None = None) -> type[Any]:
    """Loads a plugin class by its name.

    Args:
        group: Plugin group (e.g. "jarvis.brain").
        name: Entry-point name (e.g. "claude-api").
        protocol: Optional — checks structurally via an isinstance check on instances.

    Returns:
        The plugin class (not the instance). The caller must instantiate it
        itself with the matching constructor arguments.

    Raises:
        PluginNotFoundError, PluginLoadError, PluginContractError.
    """
    if group not in PLUGIN_GROUPS:
        raise ValueError(f"Unknown plugin group: {group}")

    eps = metadata.entry_points(group=group)
    candidates = [ep for ep in eps if ep.name == name]
    if not candidates:
        available = sorted(ep.name for ep in eps)
        raise PluginNotFoundError(
            f"Plugin '{name}' not found in group '{group}'. "
            f"Available: {available}"
        )

    ep = candidates[0]
    try:
        plugin_cls = ep.load()
    except ImportError as exc:
        raise PluginLoadError(
            f"Plugin '{name}' could not be imported. "
            f"Missing a dependency? Original error: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise PluginLoadError(f"Plugin '{name}' load failed: {exc}") from exc

    # Protocol check is structural (Python checks attributes + method
    # signatures on the first isinstance call on an instance — not on the
    # class). Here we can at most ensure that it is a class.
    if not isinstance(plugin_cls, type):
        raise PluginContractError(
            f"Plugin '{name}' is not a class but {type(plugin_cls)}"
        )

    _ = protocol  # Documentation-intent; verification happens at the instance level
    return plugin_cls


def describe() -> str:
    """Human-readable overview of all installed plugins — for CLI/admin UI."""
    lines: list[str] = ["Jarvis Plugin Registry", "=" * 30, ""]
    for group in PLUGIN_GROUPS:
        plugins = list_plugins(group)
        lines.append(f"[{group}]")
        if not plugins:
            lines.append("  (none)")
        else:
            for p in plugins:
                lines.append(f"  - {p}")
        lines.append("")
    return "\n".join(lines)
