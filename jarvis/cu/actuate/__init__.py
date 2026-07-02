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
raw input to this package.
"""
from jarvis.cu.actuate.base import (
    LANDING_TOLERANCE,
    ActResult,
    ActuationUnavailable,
    Actuator,
    get_actuator,
    verified_move,
)

__all__ = [
    "ActResult",
    "ActuationUnavailable",
    "Actuator",
    "LANDING_TOLERANCE",
    "get_actuator",
    "verified_move",
]
