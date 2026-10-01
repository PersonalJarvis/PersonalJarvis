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
from __future__ import annotations

import sys

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


def get_actuator() -> Actuator:
    """Resolve an actuator, yielding first when a person is using the Mac.

    The physical-input probe is macOS-only and advisory when Quartz is
    unavailable.  A positive hardware signal is different: it means the user
    is actively interacting, so Computer-Use refuses this action instead of
    fighting for mouse/keyboard ownership.  The next planner retry can proceed
    after the short quiet period.
    """
    if sys.platform == "darwin":
        from jarvis.cu.human_activity import human_input_allows_automation

        allowed, detail = human_input_allows_automation()
        if not allowed:
            raise ActuationUnavailable(
                "Pausing macOS Computer-Use because recent physical mouse or "
                f"keyboard activity was detected ({detail}). Retry when the "
                "user has stopped interacting."
            )
    return _base_get_actuator()


__all__ = [
    "ActResult",
    "ActuationUnavailable",
    "Actuator",
    "LANDING_TOLERANCE",
    "get_actuator",
    "verified_click",
    "verified_drag",
    "verified_move",
]
