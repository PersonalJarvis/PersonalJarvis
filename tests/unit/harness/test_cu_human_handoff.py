"""Deterministic human-handoff detector for Computer-Use (audit 🔴 #5).

A pure detector that recognizes a screen the *human* must handle — a CAPTCHA, a
2FA / one-time-code prompt, or a login/password entry — so the loop can hand off
instead of typing a secret it does not hold (AP-2). Reads only accessibility node
*names/roles* (every OS tree exposes them, so this is cross-platform), never a
field VALUE (a value can be a typed secret). Conservative: a bare "password" word
is not enough — a real password EDIT field or an explicit CAPTCHA/2FA phrase is
required, so an ordinary settings page mentioning "change password" does not trip
a false handoff.

This covers only the pure detector. Wiring it into the loop (pause + poll resume,
reusing the UAC elevation-clearance pattern) is the next increment.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from jarvis.harness import screenshot_only_loop as sol


def _node(role: str, name: str, value: str = "") -> Any:
    return SimpleNamespace(role=role, name=name, value=value, bounds=(0, 0, 10, 10), enabled=True)


# --- positives ---------------------------------------------------------------


def test_captcha_phrase_is_detected():
    nodes = (_node("Text", "Verify you are human"), _node("CheckBox", "I'm not a robot"))
    assert sol._human_handoff_reason(nodes) == "captcha challenge"


def test_recaptcha_label_is_detected():
    nodes = (_node("Text", "reCAPTCHA"),)
    assert sol._human_handoff_reason(nodes) == "captcha challenge"


def test_two_factor_code_is_detected():
    nodes = (_node("Text", "Enter the verification code we sent you"),)
    assert sol._human_handoff_reason(nodes) == "two-factor / one-time code"


def test_authenticator_2fa_is_detected():
    nodes = (_node("Text", "Open your authenticator app"), _node("Edit", "Code"))
    assert sol._human_handoff_reason(nodes) == "two-factor / one-time code"


def test_password_edit_field_is_login():
    nodes = (_node("Edit", "Password"), _node("Button", "Sign in"))
    assert sol._human_handoff_reason(nodes) == "login / password entry"


def test_german_passwort_edit_field_is_login():
    nodes = (_node("Edit", "Passwort"),)
    assert sol._human_handoff_reason(nodes) == "login / password entry"


def test_captcha_wins_over_login_priority():
    nodes = (_node("Edit", "Password"), _node("Text", "Please solve the captcha"))
    assert sol._human_handoff_reason(nodes) == "captcha challenge"


# --- negatives (no false handoff) --------------------------------------------


def test_bare_password_text_label_is_not_login():
    # A settings page that merely *mentions* password must not trip a handoff —
    # there is no editable password field, just a button label.
    nodes = (_node("Button", "Change password"), _node("Text", "Account settings"))
    assert sol._human_handoff_reason(nodes) is None


def test_ordinary_screen_returns_none():
    nodes = (_node("Button", "Save"), _node("Edit", "Search", value="cats"))
    assert sol._human_handoff_reason(nodes) is None


def test_empty_nodes_returns_none():
    assert sol._human_handoff_reason(()) is None
    assert sol._human_handoff_reason(None) is None


def test_value_is_never_read_for_secrets():
    # Even if a field VALUE happens to contain "captcha"/"password", only NAMES
    # are inspected — so a value never drives a handoff (AP-2: a value is a secret).
    nodes = (_node("Edit", "Search", value="how to solve a captcha and password"),)
    assert sol._human_handoff_reason(nodes) is None


# --- is_password flag (a11y foundation) drives detection too ------------------


def _secure_node(name: str = "", *, is_password: bool = True) -> Any:
    return SimpleNamespace(
        role="Edit", name=name, value="", is_password=is_password,
        focused=False, bounds=(0, 0, 10, 10), enabled=True,
    )


def test_secure_edit_flag_is_login_even_without_label():
    # An unlabeled password field (empty UIA Name) is caught by the is_password
    # flag — the name heuristic alone would miss it.
    nodes = (_secure_node(name=""),)
    assert sol._human_handoff_reason(nodes) == "login / password entry"


def test_secure_edit_flag_beats_missing_name_tokens():
    nodes = (_secure_node(name="PIN"), _node("Button", "Sign in"))
    assert sol._human_handoff_reason(nodes) == "login / password entry"


def test_is_password_false_does_not_trip_on_ordinary_field():
    nodes = (_secure_node(name="Search", is_password=False),)
    assert sol._human_handoff_reason(nodes) is None
