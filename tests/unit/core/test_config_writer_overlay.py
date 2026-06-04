"""set_overlay_style persists [ui].orb_style, comment/section-safe."""
from __future__ import annotations

import tomllib

from jarvis.core import config_writer


def test_set_overlay_style_round_trip(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text('[ui]\norb_style = "mascot"\n', encoding="utf-8")
    config_writer.set_overlay_style("whisper_bar", path=cfg)
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["ui"]["orb_style"] == "whisper_bar"


def test_set_overlay_style_creates_section_and_preserves_siblings(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text('[brain]\nprimary = "gemini"\n', encoding="utf-8")
    config_writer.set_overlay_style("none", path=cfg)
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["ui"]["orb_style"] == "none"
    assert data["brain"]["primary"] == "gemini"
