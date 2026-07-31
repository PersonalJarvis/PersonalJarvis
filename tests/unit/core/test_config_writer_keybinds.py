"""config_writer.set_keybind — persist Call/Hangup keybinds to [trigger]."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core import config_writer


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_set_keybind_call_writes_hotkey_call(tmp_path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text('[trigger]\nhotkey = "ctrl+right_alt+j"\n', encoding="utf-8")
    config_writer.set_keybind("call", "f7+f8", path=toml)
    assert 'hotkey_call = "f7+f8"' in _read(toml)


def test_set_keybind_hangup_writes_hotkey_hangup(tmp_path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[trigger]\n", encoding="utf-8")
    config_writer.set_keybind("hangup", "ctrl+shift+h", path=toml)
    assert 'hotkey_hangup = "ctrl+shift+h"' in _read(toml)


def test_set_keybind_dictate_writes_hotkey_dictate(tmp_path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[trigger]\n", encoding="utf-8")
    config_writer.set_keybind("dictate", "ctrl+alt+d", path=toml)
    assert 'hotkey_dictate = "ctrl+alt+d"' in _read(toml)


def test_set_keybind_dictate_toggle_writes_its_own_key(tmp_path) -> None:
    """Its own TOML key, not a rider on hotkey_dictate: a user may arm a hold
    key and a hands-free key at the same time."""
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[trigger]\n", encoding="utf-8")
    config_writer.set_keybind("dictate_toggle", "ctrl+shift+d", path=toml)
    written = _read(toml)
    assert 'hotkey_dictate_toggle = "ctrl+shift+d"' in written
    assert "\nhotkey_dictate =" not in written


def test_every_registered_action_is_writable(tmp_path) -> None:
    """A registry entry with no TOML key would save in memory and vanish on
    restart — the failure mode this mapping exists to prevent."""
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[trigger]\n", encoding="utf-8")
    for action in config_writer.KEYBIND_ACTIONS:
        assert action in config_writer.KEYBIND_TOML_KEY
        config_writer.set_keybind(action, "f7+f8", path=toml)
        assert f'{config_writer.KEYBIND_TOML_KEY[action]} = "f7+f8"' in _read(toml)


def test_set_keybind_rejects_retired_ptt_action(tmp_path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[trigger]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        config_writer.set_keybind("ptt", "ctrl+alt+m", path=toml)


def test_set_keybind_unknown_action_raises(tmp_path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[trigger]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        config_writer.set_keybind("bogus", "f1+f2", path=toml)
