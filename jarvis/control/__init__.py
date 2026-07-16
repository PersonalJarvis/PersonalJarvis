"""Control layer (Phase 5 Capability 5).

Bundles kill switch, CancelToken, CostMeter, and KillSwitch aggregator.

ADRs: 0004 (kill propagation), 0006 (cost hook).
Protocol definitions: `jarvis.core.protocols.CancelToken` / `CostMeter`.
"""
from __future__ import annotations

from .cancel import CancelScope, CancelToken, KillSwitch, get_kill_switch
from .cost import BudgetConfig, CooldownState, CostMeter, ModelPrice
from .wiring import (
    DEFAULT_KILL_HOTKEY,
    run_kill_hotkey_trigger,
    voice_matches_kill_intent,
    wire_kill_switch_on_bus,
    wire_tray_kill_switch,
    wire_voice_kill_switch,
)

__all__ = [
    "DEFAULT_KILL_HOTKEY",
    "BudgetConfig",
    "CancelScope",
    "CancelToken",
    "CooldownState",
    "CostMeter",
    "KillSwitch",
    "ModelPrice",
    "get_kill_switch",
    "run_kill_hotkey_trigger",
    "voice_matches_kill_intent",
    "wire_kill_switch_on_bus",
    "wire_tray_kill_switch",
    "wire_voice_kill_switch",
]
