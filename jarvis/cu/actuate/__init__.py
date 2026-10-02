"""Platform-native input actuation for Computer-Use v2.

One primitive vocabulary (`move / click / drag / scroll / key_combo /
type_text` + `cursor_pos` read-back) with a backend per platform:

* Windows — ``SendInput`` with absolute virtual-desktop positioning
  (negative-origin monitors included) and ``KEYEVENTF_UNICODE`` typing.
* macOS / Linux-X11 — ``pynput`` (Quartz points / X11 pixels, no
  primary-screen clamping), with a best-effort ``pyautogui`` fallback.
* Wayland / headless — honest refusal with an actionable message.

The backends are pure input dispatch: no overlay, no risk gating. The CU
tools remain the ToolExecutor-gated choke points (AP-3) and delegate their
raw input to this package. On macOS the common facade adds a per-action
physical-input ownership guard, so even a caller that caches the returned
actuator yields when the user takes over mouse or keyboard.
"""
from __future__ import annotations

from jarvis.cu.actuate.base import (
    LANDING_TOLERANCE,
    ActResult,
    ActuationUnavailable,
    Actuator,
    get_actuator as _base_get_actuator,
    verified_click,
    verified_drag,
    verified_move,
)
from jarvis.cu.actuate.handoff import (
    HUMAN_TAKEOVER_OUTCOME,
    HumanInputTakeover,
    guard_actuator,
    human_takeover_detail,
    human_takeover_tool_result,
    require_human_input_clear,
)


def get_actuator() -> Actuator:
    """Resolve the platform backend and apply the macOS takeover boundary.

    Ownership is checked by the returned actuator immediately before each
    mutating primitive, not only here. This is deliberate: long-lived screen
    runners cache their actuator, and a person can start using the Mac after
    that cache was created.
    """
    return guard_actuator(_base_get_actuator())


__all__ = [
    "ActResult",
    "ActuationUnavailable",
    "Actuator",
    "HUMAN_TAKEOVER_OUTCOME",
    "HumanInputTakeover",
    "LANDING_TOLERANCE",
    "get_actuator",
    "human_takeover_detail",
    "human_takeover_tool_result",
    "require_human_input_clear",
    "verified_click",
    "verified_drag",
    "verified_move",
]
