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
