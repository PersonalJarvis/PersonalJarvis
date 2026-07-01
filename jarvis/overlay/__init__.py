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
