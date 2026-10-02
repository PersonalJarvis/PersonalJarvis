"""Detect recent physical mouse/keyboard activity on macOS.

Quartz exposes multiple event-source state tables. The HID system table tracks
hardware-originated input, while Jarvis synthetic events are posted through a
separate session source. Using the HID table therefore lets Computer-Use yield
when the person is actively touching the Mac without mistaking its own injected
input for human activity.

The probe is side-effect-free: it installs no event tap, consumes no input and
never requests permissions. On macOS an unavailable or invalid HID observation
is treated as unknown ownership and therefore fails closed. On other platforms
the helper remains a no-op because this module only qualifies the macOS path.
"""
from __future__ import annotations

import logging
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Short enough not to punish a normal command handoff, long enough to catch an
# actively moving mouse / typing user (hardware events repeat every few ms).
DEFAULT_HUMAN_INPUT_GRACE_S = 0.25


@dataclass(frozen=True, slots=True)
class HumanActivity:
    """One side-effect-free physical-input observation."""

    available: bool
    seconds_since_input: float | None
    recent: bool
    detail: str


def _quartz_seconds_since_input() -> float | None:
    try:
        from Quartz import (  # type: ignore[import-not-found] # noqa: PLC0415
            CGEventSourceSecondsSinceLastEventType,
            kCGAnyInputEventType,
            kCGEventSourceStateHIDSystemState,
        )
    except (ImportError, ModuleNotFoundError):  # optional Quartz bridge may be unavailable
        return None
    try:
        value = float(
            CGEventSourceSecondsSinceLastEventType(
                kCGEventSourceStateHIDSystemState,
                kCGAnyInputEventType,
            )
        )
    except Exception:  # noqa: BLE001 - diagnostics must never break actuation
        log.debug("macOS hardware-activity probe failed", exc_info=True)
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def macos_human_activity(
    *,
    grace_s: float = DEFAULT_HUMAN_INPUT_GRACE_S,
    platform: str | None = None,
    seconds_since_input: Callable[[], float | None] | None = None,
) -> HumanActivity:
    """Return whether physical input happened inside ``grace_s``.

    No event tap is installed and no input is consumed. On non-macOS hosts the
    check is intentionally a no-op. ``available=False`` on macOS means the
    caller cannot prove that Jarvis owns input and must fail closed.
    """
    if grace_s < 0:
        raise ValueError("grace_s must be >= 0")
    platform_name = platform or sys.platform
    if platform_name != "darwin":
        return HumanActivity(
            available=False,
            seconds_since_input=None,
            recent=False,
            detail="hardware-input handoff is macOS-only",
        )

    reader = seconds_since_input or _quartz_seconds_since_input
    try:
        age = reader()
    except Exception:  # noqa: BLE001 - injected probe seam / native bridge
        log.debug("macOS hardware-activity reader failed", exc_info=True)
        age = None
    if age is None:
        return HumanActivity(
            available=False,
            seconds_since_input=None,
            recent=False,
            detail="Quartz hardware-input timing is unavailable",
        )
    try:
        age_f = float(age)
    except (TypeError, ValueError):  # invalid native probe values fail closed
        return HumanActivity(
            available=False,
            seconds_since_input=None,
            recent=False,
            detail="Quartz returned an invalid hardware-input age",
        )
    if not math.isfinite(age_f) or age_f < 0:
        return HumanActivity(
            available=False,
            seconds_since_input=None,
            recent=False,
            detail="Quartz returned an invalid hardware-input age",
        )

    recent = age_f <= grace_s
    return HumanActivity(
        available=True,
        seconds_since_input=age_f,
        recent=recent,
        detail=(
            f"physical input {age_f:.3f}s ago; yielding to the user"
            if recent
            else f"last physical input was {age_f:.3f}s ago"
        ),
    )


def human_input_allows_automation(
    *,
    grace_s: float = DEFAULT_HUMAN_INPUT_GRACE_S,
    platform: str | None = None,
    seconds_since_input: Callable[[], float | None] | None = None,
) -> tuple[bool, str]:
    """Return ``(allowed, detail)`` for a pre-action human-handoff guard.

    macOS is fail-closed: an unavailable HID state is not evidence that the
    machine is idle. Non-macOS remains a no-op so the existing platform safety
    paths keep ownership of their own input policy.
    """
    platform_name = platform or sys.platform
    activity = macos_human_activity(
        grace_s=grace_s,
        platform=platform_name,
        seconds_since_input=seconds_since_input,
    )
    if platform_name != "darwin":
        return True, activity.detail
    if not activity.available:
        return False, f"physical-input state unknown: {activity.detail}"
    return (not activity.recent), activity.detail


__all__ = [
    "DEFAULT_HUMAN_INPUT_GRACE_S",
    "HumanActivity",
    "human_input_allows_automation",
    "macos_human_activity",
]
