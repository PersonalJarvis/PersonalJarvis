"""CU capture strategy mapping (Problem 1, 2026-06-28).

The [computer_use].monitor policy must NOT pin the screenshot to the primary while
the click resolver (_capture_monitor_geometry) already follows the foreground
window — that mismatch makes CU film an EMPTY primary and "do nothing" when the
target app is on the secondary. ``cu_capture_strategy`` maps the policy to a
CAPTURE strategy that FOLLOWS the foreground window for both "primary" and
"foreground" (the "primary" policy additionally MOVES the target to the main
monitor via the G8 hook); "all" still captures the whole virtual desktop.
"""
from __future__ import annotations

from jarvis.vision.screenshot import cu_capture_strategy


def test_primary_policy_captures_foreground_not_pinned():
    assert cu_capture_strategy("primary") == "foreground"


def test_foreground_policy_captures_foreground():
    assert cu_capture_strategy("foreground") == "foreground"


def test_all_policy_captures_whole_virtual_desktop():
    assert cu_capture_strategy("all") == "all"


def test_unknown_policy_defaults_to_following_foreground():
    assert cu_capture_strategy("nonsense") == "foreground"
