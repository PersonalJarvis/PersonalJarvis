"""Guard: the shipped product contains no branded wake-word artifact.

Design spec 2026-07-07 (one-full-install-generic-wake): the wake system is
fully generic. Bundled assets may only be the word-agnostic openWakeWord
backbones; the resolver must not expose a brand map, an upstream-model probe,
or a special-cased wake pattern. If this test fails, a branded wake artifact
is creeping back in.
"""
from __future__ import annotations

import re
from pathlib import Path

import jarvis.speech.wake_constants as wake_constants

_REPO = Path(__file__).resolve().parents[3]
_ASSETS = _REPO / "jarvis" / "assets" / "wakeword"
_WAKE_SOURCES = [
    *(_REPO / "jarvis" / "speech").glob("wake*.py"),
    *(_REPO / "jarvis" / "speech" / "rolling_whisper_wake.py",),
    *(_REPO / "jarvis" / "plugins" / "wake").glob("*.py"),
    _REPO / "jarvis" / "assets" / "__init__.py",
]
# Underscore model tokens + third-party brand names. Deliberately NOT plain
# "jarvis" (the product's own name appears in prose) and NOT "rhasspy"-free
# prose exemptions — any of these tokens in a wake source is a regression.
_BRANDS = re.compile(r"hey_jarvis|hey_rhasspy|alexa|mycroft|rhasspy", re.IGNORECASE)


def test_only_word_agnostic_backbones_are_bundled() -> None:
    onnx = sorted(p.name for p in _ASSETS.glob("*.onnx"))
    assert onnx == ["embedding_model.onnx", "melspectrogram.onnx"]


def test_brand_map_probe_and_special_case_are_gone() -> None:
    assert not hasattr(wake_constants, "KNOWN_OWW_MODELS")
    assert not hasattr(wake_constants, "match_known_oww_model")
    assert not hasattr(wake_constants, "resolve_oww_model_path")
    assert not hasattr(wake_constants, "JARVIS_WAKE_PATTERN")
    assert wake_constants.INSTANT_WAKE_PHRASES == ()
    assert wake_constants.DEFAULT_WAKE_PHRASE == ""


def test_wake_sources_carry_no_brand_tokens() -> None:
    offenders: list[str] = []
    for src in _WAKE_SOURCES:
        if not src.is_file():
            continue
        for i, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
            if _BRANDS.search(line):
                offenders.append(f"{src.name}:{i}: {line.strip()}")
    assert not offenders, "branded wake tokens found:\n" + "\n".join(offenders)
