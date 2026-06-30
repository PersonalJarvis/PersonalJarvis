"""Tests für RouterVisionConfig (Wave-1 B4).

Prüft Defaults der Pydantic-Sub-Config und die Verdrahtung in `jarvis.toml`
unter `[brain.router.vision]`. Alle Felder MÜSSEN Defaults haben — bestehende
Configs ohne die Section müssen sauber laden.
"""
from __future__ import annotations

import tomllib

from jarvis.core.config import (
    DEFAULT_CONFIG_FILE,
    RouterVisionConfig,
    load_config,
)


def test_router_vision_config_defaults():
    """RouterVisionConfig liefert ohne jegliche TOML-Daten die dokumentierten Defaults."""
    cfg = RouterVisionConfig()
    assert cfg.enabled is False  # latency fix: opt-in only (default changed)
    assert cfg.refresh_interval_s == 2.0
    assert cfg.max_staleness_s == 2.0
    assert cfg.capture_mode == "screenshot"
    assert cfg.max_image_kb == 500
    assert cfg.pause_on_idle is True
    # Voice-Phrasen DE+EN — Privacy-Mode + Resume.
    assert cfg.voice_pause_phrase_de == "privacy"
    assert cfg.voice_pause_phrase_en == "privacy mode"
    assert cfg.voice_resume_phrase_de == "du darfst wieder sehen"
    assert cfg.voice_resume_phrase_en == "vision back on"
    # Sanity: enthaelt die Plan-Schluesselbegriffe.
    assert "privacy" in cfg.voice_pause_phrase_de
    assert "vision" in cfg.voice_resume_phrase_en


def test_router_vision_config_loaded_from_jarvis_toml():
    """Lädt die globale jarvis.toml via tomllib UND via load_config() und
    prüft dass `[brain.router.vision]` korrekt in RouterVisionConfig landet.

    Zwei-Ebenen-Test:
      1. Raw-TOML — garantiert dass die Section mit erwarteten Keys drin steht.
      2. load_config() — garantiert dass Pydantic die Section auto-unmarshaled
         in `cfg.brain.router.vision` (RouterVisionConfig).
    """
    toml_path = DEFAULT_CONFIG_FILE
    assert toml_path.exists(), f"jarvis.toml nicht an {toml_path}"

    # 1. Raw-TOML-Layer — Section existiert mit den Plan-Werten.
    with toml_path.open("rb") as f:
        data = tomllib.load(f)
    vsec = data["brain"]["router"]["vision"]
    assert vsec["enabled"] is False  # latency fix: opt-in only (default changed)
    assert vsec["capture_mode"] == "screenshot"
    assert vsec["refresh_interval_s"] == 2.0
    assert vsec["max_staleness_s"] == 2.0
    assert vsec["max_image_kb"] == 500
    assert vsec["pause_on_idle"] is True
    assert vsec["voice_pause_phrase_de"] == "privacy"
    assert vsec["voice_pause_phrase_en"] == "privacy mode"
    assert vsec["voice_resume_phrase_de"] == "du darfst wieder sehen"
    assert vsec["voice_resume_phrase_en"] == "vision back on"

    # 2. load_config()-Layer — Pydantic-Auto-Unmarshal landet im richtigen Feld.
    cfg = load_config(toml_path)
    assert cfg.brain.router is not None, "brain.router-Section fehlt in jarvis.toml"
    vision = cfg.brain.router.vision
    assert isinstance(vision, RouterVisionConfig)
    assert vision.enabled is False  # latency fix: opt-in only (default changed)
    assert vision.capture_mode == "screenshot"
    assert vision.refresh_interval_s == 2.0
    assert vision.voice_pause_phrase_de == "privacy"
    assert vision.voice_resume_phrase_en == "vision back on"
