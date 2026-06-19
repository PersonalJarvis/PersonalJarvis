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
    DEFAULT_LOCALE,
    detect_text_language,
    normalize_language_tag,
    resolve_output_language,
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


# ---------------------------------------------------------------------------
# resolve_output_language: the SINGLE authoritative per-turn output language
# every spoken/written layer must consume. Precedence: explicit reply-language
# pin (de/en/es) > detected input language (text > STT tag) > default locale.
# This is the contract enforced by the "Runtime Output Language" doctrine in
# CLAUDE.md (2026-06-18 forensic: a German utterance mis-transcribed as English
# made the whole chain go English because each layer re-derived language).
# ---------------------------------------------------------------------------


def test_default_locale_is_a_supported_code() -> None:
    assert DEFAULT_LOCALE in ("de", "en", "es")


def test_explicit_pin_wins_over_detected_input_language() -> None:
    # The user selected German; STT mis-transcribed German speech as clean
    # English text — the pin must override the detection (THE 2026-06-18 bug).
    assert (
        resolve_output_language("de", "english", "Mask it up.") == "de"
    )


def test_explicit_spanish_pin_wins() -> None:
    assert resolve_output_language("es", "german", "Wie ist das Wetter?") == "es"


def test_explicit_pin_is_case_and_whitespace_insensitive() -> None:
    assert resolve_output_language("  EN ", "german", "Mach das Licht an") == "en"


@pytest.mark.parametrize("pin", ["auto", "", None, "klingon"])
def test_non_pin_falls_through_to_detection(pin: str | None) -> None:
    # "auto"/empty/None/unknown are NOT a pin → mirror the detected input.
    assert resolve_output_language(pin, "english", "Mach das Licht an") == "de"
    assert resolve_output_language(pin, "german", "Turn on the lights") == "en"


def test_auto_mode_ambiguous_text_uses_stt_tag_then_default() -> None:
    # Ambiguous text, STT tag decides; no tag at all → DEFAULT_LOCALE.
    assert resolve_output_language("auto", "german", "Spotify.") == "de"
    assert resolve_output_language("auto", None, "") == DEFAULT_LOCALE


def test_default_override_respected_in_auto_mode() -> None:
    assert resolve_output_language(None, None, "", default="es") == "es"


# ---------------------------------------------------------------------------
# Conversation stickiness: a one/two-word interjection ("Now", "Stop", a lone
# loanword) must NOT flip an established conversation's language — only a
# substantive turn switches it. Natural-flow forensic 2026-06-18: a German voice
# chat said a single English "Now" and the whole turn (ack + status + readback)
# went English.
# ---------------------------------------------------------------------------


def test_thin_english_interjection_does_not_flip_german_conversation() -> None:
    # THE bug: a one-word "Now." in a running German conversation.
    assert resolve_output_language(
        "auto", "english", "Now.", conversation_language="de"
    ) == "de"


def test_thin_two_word_interjection_inherits_conversation() -> None:
    assert resolve_output_language(
        "auto", None, "Stop now", conversation_language="de"
    ) == "de"


def test_substantive_turn_switches_conversation_language() -> None:
    # A full sentence in the other language is a real switch, not an interjection.
    assert resolve_output_language(
        "auto", "german", "What is the weather like in Berlin tomorrow?",
        conversation_language="de",
    ) == "en"


def test_german_sentence_with_english_loanword_stays_german() -> None:
    # "Startup" is a content word, not a language signal — the German structure
    # words win, so a German sentence peppered with an English noun stays German.
    assert resolve_output_language(
        "auto", None, "Mach mir bitte ein Startup-Konzept",
        conversation_language="de",
    ) == "de"


def test_thin_turn_without_conversation_falls_back_to_detection() -> None:
    # No conversation established yet → a thin turn is resolved normally.
    assert resolve_output_language("auto", None, "Now.", conversation_language="") == "en"


def test_pin_still_wins_over_conversation_stickiness() -> None:
    assert resolve_output_language(
        "en", None, "Mach das Licht an", conversation_language="de"
    ) == "en"


def test_conversation_language_used_as_default_for_ambiguous_substantive() -> None:
    # A longer but signal-less turn (proper nouns) inherits the conversation
    # rather than snapping to the global default.
    assert resolve_output_language(
        "auto", None, "Spotify Netflix Berlin", conversation_language="de"
    ) == "de"
