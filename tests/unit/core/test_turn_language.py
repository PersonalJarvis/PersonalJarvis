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
    resolve_transcript_language,
    resolve_turn_language,
    validate_output_language,
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


# ---------------------------------------------------------------------------
# resolve_transcript_language — whose word list may DELETE tokens (2026-07-30)
# ---------------------------------------------------------------------------
#
# Stricter than resolve_turn_language on purpose: that one picks the language we
# answer IN, where a wrong guess merely sounds odd. This one picks the language
# whose filler/phonetic tables are run OVER what the user said, where a wrong
# guess removes words and reports success. So it never invents a default — an
# unplaceable tag resolves to "unknown", meaning "run no rules at all".


def test_a_wrong_provider_tag_loses_to_the_text() -> None:
    # The live shape: a cloud Whisper endpoint answering "English" for German
    # speech. Trusting the tag ran the English filler list over the sentence and
    # deleted the preposition "um" out of its middle.
    assert resolve_transcript_language(
        "English", "Kümmere dich um das Update"  # i18n-allow: fixture under test
    ) == "de"


def test_a_pinned_tag_echoed_back_loses_to_the_text() -> None:
    # `[stt].language = "de"` makes the provider echo "german" for speech in any
    # language at all (forensic 2026-06-10).
    assert resolve_transcript_language("german", "Please open the report") == "en"


def test_an_agreeing_tag_stands_as_a_code() -> None:
    assert resolve_transcript_language("German", "Mach bitte das Licht an") == "de"


def test_an_unplaceable_tag_stays_unknown_rather_than_guessing() -> None:
    # detect_text_language only knows de/en/es, so letting it overrule a French
    # tag would relabel French as whichever of the three it scored highest on and
    # then run THAT language's rules over it. "unknown" = run nothing.
    assert resolve_transcript_language("French", "Je pense que c'est bien") == "unknown"


def test_ambiguous_text_leaves_the_tag_standing() -> None:
    assert resolve_transcript_language("English", "Spotify Berlin") == "en"


def test_no_tag_at_all_is_answered_from_the_text() -> None:
    for tag in ("", None, "auto", "unknown"):
        assert resolve_transcript_language(tag, "Mach bitte das Licht an") == "de"


def test_no_text_at_all_falls_back_to_the_tag() -> None:
    assert resolve_transcript_language("German", "") == "de"
    assert resolve_transcript_language("", "") == "unknown"


# ---------------------------------------------------------------------------
# Deterministic output-language validation. The caller passes the result of
# resolve_output_language; the validator never chooses a target language.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("resolved_language", "text", "detected_language"),
    [
        (
            "de",
            "Das Wetter ist heute gut und wir sind bereit.",  # i18n-allow: German output fixture
            "de",
        ),
        ("en", "The weather is good today and we are ready.", "en"),
        ("es", "El tiempo está muy bien hoy y quiero saber más.", "es"),
        ("es", "Hoy hay información útil para mí, y está muy bien así.", "es"),
    ],
)
def test_output_language_validator_accepts_supported_languages(
    resolved_language: str,
    text: str,
    detected_language: str,
) -> None:
    result = validate_output_language(text, resolved_language=resolved_language)

    assert result.status == "match"
    assert result.detected_language == detected_language
    assert result.should_block is False


@pytest.mark.parametrize(
    ("resolved_language", "text", "detected_language"),
    [
        ("de", "Xin chào, tôi có thể giúp bạn bằng tiếng Việt.", "vi"),
        ("en", "这是一个完全错误的中文回答，需要立即阻止。", "zh"),
        ("es", "The answer is in the wrong language and should not be spoken.", "en"),
    ],
)
def test_output_language_validator_blocks_high_confidence_mismatches(
    resolved_language: str,
    text: str,
    detected_language: str,
) -> None:
    result = validate_output_language(text, resolved_language=resolved_language)

    assert result.status == "mismatch"
    assert result.detected_language == detected_language
    assert result.should_block is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Okay.",
        "Danke.",  # i18n-allow: short German output fixture
        "HTTP 500, CPU 95%, API v2.1.",
        "OpenAI GitHub Spotify HTTP Berlin",
        "```python\nfor item in items:\n    return item\n```",
        "Use `response.status_code == 500`.",
    ],
)
def test_output_language_validator_keeps_ambiguous_content_non_blocking(
    text: str,
) -> None:
    result = validate_output_language(text, resolved_language="de")

    assert result.status == "indeterminate"
    assert result.should_block is False


def test_output_language_validator_does_not_reject_a_chinese_name() -> None:
    result = validate_output_language(
        "The Beijing office uses 北京 for its local label.",
        resolved_language="en",
    )

    assert result.should_block is False
    assert result.detected_language != "zh"


@pytest.mark.parametrize(
    "text",
    [
        "Name: 中华人民共和国",
        "中华人民共和国",
        "Label: 中华人民共和国",
        "The organization 中华人民共和国 is listed with the correct English name.",
    ],
)
def test_output_language_validator_keeps_short_han_names_non_blocking(
    text: str,
) -> None:
    result = validate_output_language(text, resolved_language="en")

    assert result.detected_language != "zh"
    assert result.should_block is False


def test_output_language_validator_still_blocks_long_han_corruption() -> None:
    result = validate_output_language(
        "这是一个完全错误的中文回答必须立即阻止不能向用户播放",
        resolved_language="en",
    )

    assert result.status == "mismatch"
    assert result.detected_language == "zh"
    assert result.should_block is True


def test_output_language_validator_does_not_reject_a_vietnamese_name() -> None:
    result = validate_output_language(
        "The meeting with Nguyễn Văn Bình is today.",
        resolved_language="en",
    )

    assert result.status == "match"
    assert result.detected_language == "en"
    assert result.should_block is False


def test_output_language_validator_does_not_misread_repeated_the_as_vietnamese() -> None:
    text = "The answer is the same as the question, and the result is clear."

    result = validate_output_language(text, resolved_language="en")

    assert result.status == "match"
    assert result.detected_language == "en"
    assert result.should_block is False


@pytest.mark.parametrize(
    "text",
    [
        "the the the the the",
        "ban ban ban ban ban",
        "bạn bạn bạn bạn bạn",
    ],
)
def test_output_language_validator_does_not_count_repeated_tokens_as_vietnamese(
    text: str,
) -> None:
    result = validate_output_language(text, resolved_language="en")

    assert result.detected_language != "vi"
    assert result.should_block is False


def test_output_language_validator_keeps_unknown_target_non_blocking() -> None:
    result = validate_output_language(
        "这是一个很长的中文回答，应当保持安全。",
        resolved_language="fr",
    )

    assert result.status == "indeterminate"
    assert result.resolved_language == "unknown"
    assert result.should_block is False
