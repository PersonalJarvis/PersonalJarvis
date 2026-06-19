"""Localized spoken fallback when the WHOLE brain provider chain fails.

Live forensic 2026-06-01 (data/jarvis_desktop.log 23:35): Gemini (the active
provider) failed on tool-name validation, the fallback chain hit claude-api
(401) + grok (403), and the developer billing diagnostic — "Account-Problem
bei grok … console.x.ai/team/billing" — was SPOKEN aloud. A voice butler must
never read provider names or billing URLs; it speaks a short, provider-
agnostic apology in the user's SELECTED reply language (de/en/es; "auto" → de)
with three variants so repeated failures don't sound robotic.
"""
from __future__ import annotations

import pytest

from jarvis.brain.manager import (
    BrainManager,
    _PROVIDER_DOWN_PHRASES,
    _provider_down_phrase,
)

# The exact leak tokens emitted by _format_provider_chain_error — none may
# survive into the spoken phrase.
_JARGON = (
    "grok", "anthropic", "openai", "openrouter", "gemini", "xai",
    "console.", "http", "billing", "credit",
)


class TestProviderDownPhrase:
    @pytest.mark.parametrize("lang", ["de", "en", "es"])
    def test_three_variants_per_supported_language(self, lang: str) -> None:
        assert len(_PROVIDER_DOWN_PHRASES[lang]) == 3
        assert len(set(_PROVIDER_DOWN_PHRASES[lang])) == 3  # all distinct

    @pytest.mark.parametrize("lang", ["de", "en", "es"])
    def test_rotation_is_deterministic_and_cycles(self, lang: str) -> None:
        got = [_provider_down_phrase(lang, i) for i in range(6)]
        assert got[0] == got[3] and got[1] == got[4] and got[2] == got[5]
        assert len({got[0], got[1], got[2]}) == 3

    def test_auto_falls_back_to_german(self) -> None:
        assert _provider_down_phrase("auto", 0) == _PROVIDER_DOWN_PHRASES["de"][0]

    def test_unknown_language_falls_back_to_german(self) -> None:
        assert _provider_down_phrase("fr", 1) == _PROVIDER_DOWN_PHRASES["de"][1]

    @pytest.mark.parametrize("lang", ["de", "en", "es", "auto", "FR"])
    def test_phrase_is_voice_safe_no_provider_jargon(self, lang: str) -> None:
        for i in range(3):
            low = _provider_down_phrase(lang, i).lower()
            for bad in _JARGON:
                assert bad not in low, (lang, i, bad)

    def test_spanish_diacritics_preserved(self) -> None:
        # Orthographic correctness — never ASCII-fold Spanish.
        joined = " ".join(_PROVIDER_DOWN_PHRASES["es"])
        assert any(c in joined for c in "áéíóúñ¿¡")


class TestNextProviderDownPhraseAutoDetect:
    """The METHOD ``_next_provider_down_phrase`` must speak the auto-detected
    turn language, not always German, when no pin is set (Runtime Output
    Language: an English/Spanish 'auto' user hears a total-failure apology in
    their own language). A pin still wins.
    """

    @staticmethod
    def _manager(reply_language: str, turn_lang: str) -> BrainManager:
        m = BrainManager.__new__(BrainManager)
        m._reply_language = reply_language
        m._turn_detected_lang = turn_lang
        m._provider_down_idx = 0
        return m

    def test_auto_mode_uses_detected_turn_language(self) -> None:
        m = self._manager("auto", "en")
        assert m._next_provider_down_phrase() == _PROVIDER_DOWN_PHRASES["en"][0]

    def test_auto_mode_spanish_turn(self) -> None:
        m = self._manager("auto", "es")
        assert m._next_provider_down_phrase() == _PROVIDER_DOWN_PHRASES["es"][0]

    def test_auto_mode_unknown_turn_keeps_german_default(self) -> None:
        m = self._manager("auto", "")
        assert m._next_provider_down_phrase() == _PROVIDER_DOWN_PHRASES["de"][0]

    def test_pin_wins_over_detected_turn_language(self) -> None:
        m = self._manager("de", "en")
        assert m._next_provider_down_phrase() == _PROVIDER_DOWN_PHRASES["de"][0]
