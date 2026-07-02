"""TriggerConfig keybind fields + resolve_hotkeys (configurable Call/Hangup/PTT)."""
from __future__ import annotations

from jarvis.core.config import TriggerConfig


def test_defaults_match_legacy_hardcoded_values() -> None:
    t = TriggerConfig()
    assert t.hotkey == "ctrl+right_alt+j"
    assert t.hotkey_call == "f3+f4"
    assert t.hotkey_hangup == "f1+f2"


def test_resolve_hotkeys_ptt_on_uses_call_field() -> None:
    t = TriggerConfig(push_to_talk=True, hotkey="ctrl+right_alt+j", hotkey_call="f7+f8")
    call, ptt = t.resolve_hotkeys()
    assert call == ("f7+f8",)
    assert ptt == ("ctrl+right_alt+j",)


def test_resolve_hotkeys_ptt_off_has_two_call_combos_no_ptt() -> None:
    t = TriggerConfig(push_to_talk=False, hotkey="ctrl+shift+space", hotkey_call="f7+f8")
    call, ptt = t.resolve_hotkeys()
    assert call == ("ctrl+shift+space", "f7+f8")
    assert ptt == ()


def test_old_toml_without_new_keys_keeps_legacy_behaviour() -> None:
    # A config built only from the legacy keys must behave exactly as before.
    t = TriggerConfig(hotkey="ctrl+right_alt+j", push_to_talk=True)
    call, ptt = t.resolve_hotkeys()
    assert call == ("f3+f4",)
    assert ptt == ("ctrl+right_alt+j",)
    assert t.hotkey_hangup == "f1+f2"


def test_resolve_hotkeys_drops_blank_call_when_ptt_on() -> None:
    t = TriggerConfig(push_to_talk=True, hotkey="ctrl+right_alt+j", hotkey_call="")
    call, ptt = t.resolve_hotkeys()
    assert call == ()
    assert ptt == ("ctrl+right_alt+j",)


def test_resolve_hotkeys_drops_blank_ptt_when_ptt_on() -> None:
    t = TriggerConfig(push_to_talk=True, hotkey="", hotkey_call="f3+f4")
    call, ptt = t.resolve_hotkeys()
    assert call == ("f3+f4",)
    assert ptt == ()


def test_resolve_hotkeys_drops_blank_entries_when_ptt_off() -> None:
    t = TriggerConfig(push_to_talk=False, hotkey="", hotkey_call="f3+f4")
    call, ptt = t.resolve_hotkeys()
    assert call == ("f3+f4",)
    assert ptt == ()
