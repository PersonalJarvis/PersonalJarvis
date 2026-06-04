"""Brain-specific tool definitions that do not go through `entry_points`.

Currently housed here: self-mod tools (Phase 7.3) — they are tightly coupled to
`AtomicConfigWriter` and `PendingMutationStore` and are called exclusively by
the main-Jarvis tier (Plan-§AD-2).

Plan-drift note (assumption A-7): generic plugin tools live under
`jarvis/plugins/tool/` with `entry_points` discovery. Self-mod tools
require shared state (writer + pending-store) and are therefore
centrally instantiable here — see `build_self_mod_tools()`.
"""
from __future__ import annotations

from .self_mod_tools import (
    SELF_MOD_TOOL_NAMES,
    GetConfigValueTool,
    ListMutableSettingsTool,
    SetConfigValueTool,
    build_self_mod_tools,
)
from .skill_authoring import SpawnSkillAuthorTool

__all__ = [
    "SELF_MOD_TOOL_NAMES",
    "GetConfigValueTool",
    "ListMutableSettingsTool",
    "SetConfigValueTool",
    "SpawnSkillAuthorTool",
    "build_self_mod_tools",
]
