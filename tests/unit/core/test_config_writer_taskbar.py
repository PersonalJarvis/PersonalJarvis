"""DuckingConfig defaults + config_writer setters for the Taskbar toggles."""
from __future__ import annotations

import tomllib

from jarvis.core import config_writer
from jarvis.core.config import DuckingConfig, JarvisConfig


def test_ducking_config_defaults():
    d = DuckingConfig()
    assert d.enabled is False
    assert d.restore_delay_ms == 400
    assert d.never_mute == []
    assert JarvisConfig().ducking.enabled is False


def test_set_mute_music_round_trip(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text('[ui]\norb_style = "whisper_bar"\n', encoding="utf-8")
    config_writer.set_mute_music(True, path=cfg)
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["ducking"]["enabled"] is True
    assert data["ui"]["orb_style"] == "whisper_bar"  # sibling preserved


def test_set_bar_persistent_round_trip(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text('[ui]\norb_style = "whisper_bar"\n', encoding="utf-8")
    config_writer.set_bar_persistent(False, path=cfg)
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["ui"]["bar_persistent"] is False
