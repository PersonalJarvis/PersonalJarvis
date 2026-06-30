"""English-only STT model must never be used for a non-English / auto user.

Forensic 2026-06-28 (clipboard screenshot + jarvis.toml): the post-wake STT ran
``distil-large-v3`` — an ENGLISH-ONLY Distil-Whisper checkpoint — while the user
spoke German. An English-only Whisper model cannot emit German: fed German audio
it phonetically mangles it into English words. The live "Listening" bubble showed
the German "Kannst du mir bitte ..." as "Can't you me my ... Can't you me pl ...".
``[stt].language = "auto"`` did NOT help, because auto-detect on a model that only
knows English still only yields English.

Defense-in-depth: ``FasterWhisperProvider`` transparently swaps an English-only
model for a multilingual checkpoint whenever the recognition language is NOT a
deliberate ``en`` pin (i.e. ``auto`` / ``de`` / ``es`` / unset). A user who
explicitly pins ``en`` keeps the fast English-only model.
"""
from __future__ import annotations

import pytest

from jarvis.plugins.stt.fwhisper import (
    FasterWhisperProvider,
    _multilingual_equivalent,
)


# ---------------------------------------------------------------------------
# Helper: which models are English-only and what they map to.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # Distil-Whisper is English-only -> fast multilingual turbo.
        ("distil-large-v3", "large-v3-turbo"),
        ("distil-large-v2", "large-v3-turbo"),
        ("distil-medium.en", "large-v3-turbo"),
        ("distil-small.en", "large-v3-turbo"),
        # Plain ``*.en`` Whisper sizes -> drop the suffix (same size, multilingual).
        ("base.en", "base"),
        ("small.en", "small"),
        ("medium.en", "medium"),
        # Already-multilingual models are left untouched.
        ("large-v3-turbo", None),
        ("large-v3", None),
        ("base", None),
        ("small", None),
        # Opaque HuggingFace repo ids are never rewritten.
        ("Systran/faster-whisper-large-v3", None),
    ],
)
def test_multilingual_equivalent(model: str, expected: str | None) -> None:
    assert _multilingual_equivalent(model) == expected


# ---------------------------------------------------------------------------
# Constructor guard: non-English / auto upgrades; explicit ``en`` keeps it.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language", ["auto", None, "de", "es"])
def test_english_only_model_upgraded_for_non_english(language: str | None) -> None:
    p = FasterWhisperProvider(model="distil-large-v3", language=language)
    assert p._model_name == "large-v3-turbo"


def test_english_only_model_kept_for_explicit_english_pin() -> None:
    # A user who deliberately speaks only English keeps the fast English model.
    p = FasterWhisperProvider(model="distil-large-v3", language="en")
    assert p._model_name == "distil-large-v3"


def test_dot_en_model_stripped_to_multilingual_for_auto() -> None:
    p = FasterWhisperProvider(model="base.en", language="auto")
    assert p._model_name == "base"


def test_multilingual_model_is_untouched() -> None:
    p = FasterWhisperProvider(model="large-v3-turbo", language="de")
    assert p._model_name == "large-v3-turbo"


def test_bare_default_provider_is_multilingual_for_autodetect() -> None:
    # The bare ``FasterWhisperProvider()`` (auto-detect) must not silently fall
    # onto an English-only model — the default path a bilingual user hits.
    p = FasterWhisperProvider()
    assert _multilingual_equivalent(p._model_name) is None
