"""Generic modal / banner detection for Computer-Use (audit 🟠 #15).

A cookie/consent banner, a "save changes?" prompt, or a small confirm dialog in
front swallows the agent's clicks until handled. ``_modal_dialog_hint`` detects
it from the foreground control LABELS (cross-platform, no role needed) and
returns a one-time "handle the dialog first" note. Conservative: an ordinary page
with a lone OK button (or a large control set) must not trip it.
"""
from __future__ import annotations

from jarvis.harness import screenshot_only_loop as sol


# --- positives ---------------------------------------------------------------


def test_save_prompt_is_detected():
    hint = sol._modal_dialog_hint(["Save changes", "Don't save", "Cancel"])
    assert hint is not None
    assert "save" in hint.lower()


def test_cookie_banner_is_detected():
    hint = sol._modal_dialog_hint(["Accept all", "Reject all", "Manage preferences"])
    assert hint is not None
    assert "cookie" in hint.lower() or "consent" in hint.lower()


def test_small_confirm_dialog_is_detected():
    hint = sol._modal_dialog_hint(["OK", "Cancel"])
    assert hint is not None
    assert "confirmation" in hint.lower()


def test_yes_no_pair_is_a_confirm_dialog():
    assert sol._modal_dialog_hint(["Yes", "No"]) is not None


def test_save_wins_over_cookie_priority():
    # Both clusters present -> the save prompt (more urgent/destructive) wins.
    hint = sol._modal_dialog_hint(["Save changes", "Accept all cookies", "Cancel"])
    assert "save" in hint.lower()


# --- negatives (no false nudge) ----------------------------------------------


def test_ordinary_page_returns_none():
    assert sol._modal_dialog_hint(
        ["File", "Edit", "View", "Search", "Settings", "Profile"]
    ) is None


def test_lone_ok_button_is_not_a_dialog():
    assert sol._modal_dialog_hint(["OK"]) is None


def test_confirm_pair_in_large_control_set_is_not_a_dialog():
    # A positive+negative pair inside a big app window is not a focused modal.
    labels = ["OK", "Cancel"] + [f"Item {i}" for i in range(10)]
    assert sol._modal_dialog_hint(labels) is None


def test_empty_and_none_return_none():
    assert sol._modal_dialog_hint([]) is None
    assert sol._modal_dialog_hint(None) is None


def test_close_button_alone_is_not_negative_confirm():
    # "Close" (a window X) is deliberately NOT a confirm-negative, so an OK+Close
    # pair must not read as a dialog.
    assert sol._modal_dialog_hint(["OK", "Close"]) is None
