"""Overlay-Bridge Public-API. Plan §9.1.

Hauptjarvis-Code importiert ausschliesslich von hier::

    from jarvis.overlay import (
        get_overlay,
        start_overlay, stop_overlay,
        overlay_action, overlay_action_sync, overlay_action_scope,
        ActionKind,
    )

Sub-Agent-Detection (Plan §8.7 / AD-6) ist transparent: in Sub-Agents
returnt ``get_overlay()`` einen ``NoOpOverlayBridge``-Stub mit der
gleichen API. Caller-Code merkt strukturell nichts.
"""

from __future__ import annotations

from .bridge import (
    NoOpOverlayBridge,
    OverlayBridge,
    is_sub_agent_process,
)
from .integration import (
    get_overlay,
    is_overlay_enabled,
    set_overlay,
    start_overlay,
    stop_overlay,
)
from .supervisor import OverlaySupervisor
from .triggers import (
    ActionKind,
    overlay_action,
    overlay_action_scope,
    overlay_action_scope_sync,
    overlay_action_sync,
)

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
