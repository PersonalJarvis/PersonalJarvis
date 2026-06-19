"""Bilingual STT contract: ``language="auto"`` must NOT force a language.

Live forensic 2026-06-14 14:24 (data/jarvis_desktop.log): with
``[stt].language = "de"`` the cloud Groq Whisper was forced to transcribe
EVERY utterance as German. Clear English usually survived, but marginal/short
English audio was mangled into German tokens — e.g. the English "Hello, what's
the weather like in Melbourne?" came out as
``'Hallo, was ist der West-Like in Melbourne?'`` (confidence 0.654), and
"Is it currently winter in Melbourne?" became ``'Ist es gerade Winter in
Melbourne?'``. The text itself was corrupted at the STT layer, so the
downstream ``resolve_turn_language`` (which trusts the transcribed text) had no
English signal left to recover.

The fix is to let Whisper auto-detect per utterance: ``language = "auto"``
must resolve to "no forced language" (``None``) so English audio yields English
text and German audio yields German text. A real ISO pin (de/en/es) must still
force that language for users who deliberately want it. The always-on wake path
is openWakeWord (a neural model, language-independent), so auto-detect on the
post-wake utterance never reintroduces the "Hey Jarvis" -> "Thank you"
short-chunk hallucination that the pin was guarding against.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.config import STTConfig
from jarvis.plugins.stt import build_stt_from_config
from jarvis.plugins.stt.groq_api import GroqWhisperAPI

# ---------------------------------------------------------------------------
# Provider-level contract: "auto"/"" -> auto-detect; ISO pin -> forced.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("auto", None),
        ("", None),
        (None, None),
        ("de", "de"),
        ("en", "en"),
        ("es", "es"),
    ],
)
def test_groq_language_pin_maps_to_autodetect_or_force(
    language: str | None, expected: str | None
) -> None:
    provider = GroqWhisperAPI(language=language)
    assert provider._language == expected


# ---------------------------------------------------------------------------
# Factory-level contract: build_stt_from_config honours the same mapping.
# ---------------------------------------------------------------------------

def _cfg(language: str) -> SimpleNamespace:
    return SimpleNamespace(
        provider="groq-api",
        model="large-v3-turbo",
        device="cpu",
        compute_type="int8",
        language=language,
        bias_prompt="",
    )


def test_factory_auto_does_not_force_a_language() -> None:
    """The bilingual default: "auto" must leave the provider auto-detecting,
    never forcing a single language onto the other language's audio."""
    stt = build_stt_from_config(_cfg("auto"))
    assert getattr(stt, "_language", "MISSING") is None


def test_factory_explicit_pin_is_honoured() -> None:
    stt = build_stt_from_config(_cfg("de"))
    assert getattr(stt, "_language", "MISSING") == "de"


# ---------------------------------------------------------------------------
# Shipped default must stay bilingual-friendly.
# ---------------------------------------------------------------------------

def test_shipped_stt_default_is_auto() -> None:
    assert STTConfig().language == "auto"
