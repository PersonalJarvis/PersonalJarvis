"""Turn-language resolution (live forensic 2026-06-10 23:12, data/jarvis_desktop.log).

``[stt].language = "de"`` pins Groq Whisper to German, and Whisper echoes the
pin back in its response — so EVERY transcript was tagged ``language=german``,
even ``text="What's weather like tomorrow?"``. The pipeline trusted that tag
(``lang = transcript.language``) and drove the ack-brain, TTS voice and phrase
pickers with the wrong language.

``resolve_turn_language`` fixes this at the root: the transcribed TEXT decides
when it is clearly one language; the STT tag is only a tie-breaker for
ambiguous text (single proper nouns etc.). It also normalizes the two tag
shapes seen live — Whisper language NAMES ("german") from the cloud API vs
ISO codes ("de") from local faster-whisper — to codes, so downstream maps like
``{"de": "de-DE"}.get(lang)`` (TTS voice pin) stop silently missing.
"""
from __future__ import annotations

import pytest

from jarvis.core.turn_language import (
    detect_text_language,
    normalize_language_tag,
    resolve_turn_language,
)

# ---------------------------------------------------------------------------
# Tag normalization: names ("german") and codes ("de") → codes.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("german", "de"),
        ("German", "de"),
        ("deutsch", "de"),  # i18n-allow: language-name fixture
        ("de", "de"),
        ("de-DE", "de"),
        ("english", "en"),
        ("en", "en"),
        ("en-US", "en"),
        ("spanish", "es"),
        ("es", "es"),
        ("", "unknown"),
        (None, "unknown"),
        ("klingon", "unknown"),
    ],
)
def test_normalize_language_tag(tag: str | None, expected: str) -> None:
    assert normalize_language_tag(tag) == expected


# ---------------------------------------------------------------------------
# Text heuristic: clear-cut utterances are decidable from text alone.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What's weather like tomorrow?", "en"),
        (
            "Hey, what's the weather like today? Please give me an honest "
            "review and tell me what's the weather.",
            "en",
        ),
        ("Wie ist das Wetter morgen?", "de"),  # i18n-allow: German voice fixture
        ("Oeffne bitte den Browser und zeig mir die Logs", "de"),  # i18n-allow: fixture
        ("Mach das Licht an", "de"),  # i18n-allow: German voice fixture
        ("¿Qué tiempo hace mañana en Madrid?", "es"),
        ("Spotify.", "unknown"),
        ("", "unknown"),
        ("GitHub", "unknown"),
    ],
)
def test_detect_text_language(text: str, expected: str) -> None:
    assert detect_text_language(text) == expected


def test_umlauts_bias_german() -> None:
    # Script hint: umlauts/ß are a strong German signal even without
    # function-word overlap.
    text = "Müllabfuhr Königstraße"  # i18n-allow: German voice fixture
    assert detect_text_language(text) == "de"


# ---------------------------------------------------------------------------
# Resolution: text wins when decisive, STT tag breaks ties, default last.
# ---------------------------------------------------------------------------

def test_english_text_beats_pinned_german_stt_tag() -> None:
    """THE live bug: STT pinned to de tags English speech as 'german'."""
    assert resolve_turn_language("german", "What's weather like tomorrow?") == "en"


def test_german_text_beats_wrong_english_stt_tag() -> None:
    text = "Mach bitte das Licht im Wohnzimmer an"  # i18n-allow: German voice fixture
    assert resolve_turn_language("english", text) == "de"


def test_ambiguous_text_falls_back_to_stt_tag() -> None:
    assert resolve_turn_language("german", "Spotify.") == "de"


def test_codes_pass_through_on_ambiguous_text() -> None:
    assert resolve_turn_language("de", "ok") == "de"


def test_unknown_everything_falls_back_to_default() -> None:
    assert resolve_turn_language(None, "") == "en"
    assert resolve_turn_language("unknown", "Hmm", default="de") == "de"
