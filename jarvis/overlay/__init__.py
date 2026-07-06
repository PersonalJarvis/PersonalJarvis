"""Overlay bridge public API. Plan §9.1.

Main-Jarvis code imports exclusively from here::

    from jarvis.overlay import (
        get_overlay,
        start_overlay, stop_overlay,
        overlay_action, overlay_action_sync, overlay_action_scope,
        ActionKind,
    )

Sub-agent detection (Plan §8.7 / AD-6) is transparent: in sub-agents
``get_overlay()`` returns a ``NoOpOverlayBridge`` stub with the
same API. Caller code notices nothing structurally.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

# PEP 562 lazy re-exports. The eager form pulled ``.bridge`` -> ``.schema`` ->
# the OS-Level ``overlay.schema`` package (+ its ``ulid`` dependency) into
# EVERY import of ANY ``jarvis.overlay`` submodule — so a host without the
# OS-Level editable install could not even import ``virtual_cursor`` /
# ``system_cursor``, which silently killed the Computer-Use mouse tools
# (click / click_element / move_mouse) at plugin load (2026-07-06 forensic:
# the tools loaded only on the maintainer's interpreter, where a manual
# ``pip install -e OS-Level`` existed). Lazy resolution keeps the public API
# identical while only the names someone actually uses pay their import cost.
_EXPORTS: dict[str, str] = {
    "NoOpOverlayBridge": ".bridge",
    "OverlayBridge": ".bridge",
    "is_sub_agent_process": ".bridge",
    "get_overlay": ".integration",
    "is_overlay_enabled": ".integration",
    "set_overlay": ".integration",
    "start_overlay": ".integration",
    "stop_overlay": ".integration",
    "OverlaySupervisor": ".supervisor",
    "ActionKind": ".triggers",
    "overlay_action": ".triggers",
    "overlay_action_scope": ".triggers",
    "overlay_action_scope_sync": ".triggers",
    "overlay_action_sync": ".triggers",
}


def __getattr__(name: str) -> Any:
    submodule = _EXPORTS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(submodule, __name__), name)
    globals()[name] = value  # cache: subsequent lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))

__all__ = [
    "ActionKind",
    "NoOpOverlayBridge",
    "OverlayBridge",
    "OverlaySupervisor",
    "get_overlay",
    "is_overlay_enabled",
    "is_sub_agent_process",
    "overlay_action",
    "overlay_action_scope",
    "overlay_action_scope_sync",
    "overlay_action_sync",
    "set_overlay",
    "start_overlay",
    "stop_overlay",
]
