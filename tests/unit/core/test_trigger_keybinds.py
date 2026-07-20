"""TriggerConfig keybind fields and retired push-to-talk compatibility."""
from __future__ import annotations

from jarvis.core.config import TriggerConfig


def test_defaults_expose_only_call_and_hangup_shortcuts() -> None:
    t = TriggerConfig()
    assert t.hotkey == ""
    assert t.hotkey_call == "f3+f4"
    assert t.hotkey_hangup == "f1+f2"
    assert t.push_to_talk is False


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
