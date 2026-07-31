"""TriggerConfig keybind fields and retired push-to-talk compatibility."""
from __future__ import annotations

from jarvis.core.config import TriggerConfig


def test_defaults_expose_the_four_shipped_shortcuts() -> None:
    t = TriggerConfig()
    assert t.hotkey == ""
    assert t.hotkey_call == "f3+f4"
    assert t.hotkey_hangup == "f1+f2"
    assert t.push_to_talk is False


def test_both_dictation_shortcuts_ship_bound() -> None:
    """Maintainer directive 2026-07-28: dictation is no longer invisible on a
    fresh install. Curated combos, valid on all three platforms, no collisions
    (proved in tests/unit/ui/test_keybinds_route.py)."""
    t = TriggerConfig()
    assert t.hotkey_dictate == "ctrl+right_alt+j"
    assert t.hotkey_dictate_toggle == "ctrl+right_alt+space"


def test_a_cleared_dictation_shortcut_is_still_a_valid_state() -> None:
    """Clearing a row in the Shortcuts tab must not be rejected — dictation
    still runs from the bar, the UI and `jarvis api dictation start`."""
    t = TriggerConfig(hotkey_dictate="", hotkey_dictate_toggle="")
    assert t.hotkey_dictate == ""
    assert t.hotkey_dictate_toggle == ""


def test_an_existing_install_keeps_its_own_dictation_keys() -> None:
    """The new defaults must never overwrite a value the user already chose."""
    t = TriggerConfig(hotkey_dictate="ctrl+shift+d", hotkey_dictate_toggle="")
    assert t.hotkey_dictate == "ctrl+shift+d"
    assert t.hotkey_dictate_toggle == ""


def test_resolve_hotkeys_ignores_legacy_push_to_talk_values() -> None:
    t = TriggerConfig(push_to_talk=True, hotkey="ctrl+right_alt+j", hotkey_call="f7+f8")
    call, ptt = t.resolve_hotkeys()
    assert call == ("f7+f8",)
    assert ptt == ()


def test_old_config_values_remain_parseable_but_are_not_armed() -> None:
    t = TriggerConfig(hotkey="ctrl+right_alt+j", push_to_talk=True)
    call, ptt = t.resolve_hotkeys()
    assert call == ("f3+f4",)
    assert ptt == ()
    assert t.hotkey_hangup == "f1+f2"


def test_resolve_hotkeys_drops_a_cleared_call_shortcut() -> None:
    t = TriggerConfig(push_to_talk=True, hotkey="ctrl+right_alt+j", hotkey_call="")
    call, ptt = t.resolve_hotkeys()
    assert call == ()
    assert ptt == ()
