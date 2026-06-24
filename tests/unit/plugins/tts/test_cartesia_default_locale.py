"""Cartesia must never silently fall back to its English voice on un-sniffable
text — that is the British-accent-on-German symptom (forensic 2026-06-23).

When neither a per-call ``language_code`` pin nor the cheap text-detection
heuristic resolves a language, the voice fallback follows the configured
``default_locale`` (derived from ``[tts.cartesia].language``), not a hardcoded
English voice. The real defense is the pipeline always passing a concrete
``language_code``; this is the safety net for the rare unpinned path.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.plugins.tts.cartesia_tts import CartesiaTTS


@pytest.fixture
def patched_secret():
    with patch(
        "jarvis.plugins.tts.cartesia_tts.cfg.get_secret", return_value="sk-test"
    ):
        yield


def _tts(language: str) -> CartesiaTTS:
    return CartesiaTTS(
        voice_id="GENERIC",
        voice_id_de="DE-VOICE-UUID",
        voice_id_en="EN-VOICE-UUID",
        voice_id_es="ES-VOICE-UUID",
        language=language,
    )


def test_unsniffable_text_falls_back_to_default_locale_de(patched_secret) -> None:
    # language="de" configured; no pin, un-sniffable text → DE voice, NOT English.
    tts = _tts(language="de")
    voice = tts._resolve_voice("…", voice_override=None, language_code=None)
    assert voice == "DE-VOICE-UUID"


def test_unsniffable_text_auto_defaults_to_doctrine_locale(patched_secret) -> None:
    # language="auto" → fall back to the doctrine DEFAULT_LOCALE ("en").
    tts = _tts(language="auto")
    voice = tts._resolve_voice("…", voice_override=None, language_code=None)
    assert voice == "EN-VOICE-UUID"


def test_concrete_pin_still_wins_over_default_locale(patched_secret) -> None:
    # A concrete de-DE pin must pick the DE voice regardless of default_locale.
    tts = _tts(language="en")
    voice = tts._resolve_voice("anything", voice_override=None, language_code="de-DE")
    assert voice == "DE-VOICE-UUID"
