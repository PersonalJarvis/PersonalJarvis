"""The set of languages Jarvis can HEAR is one list, and it is not three.

Two things are pinned here.

**One source.** ``[stt].language`` (voice) and ``[dictation].language``
(dictation) are two settings for one microphone. They used to carry two
hand-written tuples, which is the drift trap that has bitten this repo before
(AP-4): the day a language is added to one, the other silently rejects it and the
user sees an option that will not save.

**Wide enough to be honest.** The list used to be ``auto/de/en/es`` — the three
locales the product *speaks*. But what Jarvis can understand is a different
question from what it can answer in, and capping recognition at three languages
meant a Mandarin, Japanese or Arabic speaker could not dictate at all. That is
the maintainer's-config-is-the-baseline mistake in miniature (CLAUDE.md §3), so
the floor is asserted, not assumed.
"""
from __future__ import annotations

from jarvis.core.config import (
    AUTO_LANGUAGE,
    DICTATION_LANGUAGES,
    RECOGNITION_LANGUAGE_CHOICES,
    RECOGNITION_LANGUAGES,
    DictationConfig,
)


def test_dictation_and_voice_offer_the_same_languages() -> None:
    from jarvis.ui.web.settings_routes import _STT_LANGUAGES

    assert tuple(DICTATION_LANGUAGES) == tuple(_STT_LANGUAGES), (
        "the two recognition-language lists drifted apart — one microphone, "
        "one list (AP-4)"
    )


def test_detection_is_offered_and_is_not_itself_a_language() -> None:
    assert RECOGNITION_LANGUAGE_CHOICES[0] == AUTO_LANGUAGE
    assert AUTO_LANGUAGE not in RECOGNITION_LANGUAGES


def test_languages_beyond_the_three_product_locales_are_selectable() -> None:
    """The regression: a picker that stops at de/en/es locks out most of earth."""
    for code in ("zh", "ja", "ar", "hi", "pt", "ru", "ko", "tr", "pl", "sv"):
        assert code in RECOGNITION_LANGUAGES, f"{code} cannot be selected"
    # A meaningful floor, so a future "simplification" back to a handful of
    # languages fails loudly instead of quietly shrinking the product.
    assert len(RECOGNITION_LANGUAGES) >= 50


def test_the_codes_are_shaped_like_language_codes() -> None:
    for code in RECOGNITION_LANGUAGES:
        assert code.isalpha() and code.islower() and 2 <= len(code) <= 3, code
    assert len(set(RECOGNITION_LANGUAGES)) == len(RECOGNITION_LANGUAGES)


def test_a_non_european_language_survives_config_validation() -> None:
    """The config validator falls back to ``auto`` for anything it does not know,
    so a language missing from the list is not an error the user ever sees — it
    silently becomes "automatic". That makes this the only place it can be
    caught."""
    assert DictationConfig(language="zh").language == "zh"
    assert DictationConfig(language="JA").language == "ja"
    # Genuinely unknown input still degrades to the always-working answer.
    assert DictationConfig(language="klingon").language == "auto"
