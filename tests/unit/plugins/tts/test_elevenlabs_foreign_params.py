"""ElevenLabs must not 400 on a FOREIGN model / voice left in shared ``[tts]``.

``[tts]`` has a single global ``model`` + ``voice_de``/``voice_en`` shared across
every TTS family. Switching FROM Cartesia/Gemini/Grok TO ElevenLabs can leave a
foreign value behind (Cartesia's ``sonic-2`` model, Gemini's ``Kore`` voice).
ElevenLabs used to forward those verbatim and the API answered ``400 — "An
invalid ID has been received for voice: 'sonic-2'"`` on every call → the provider
read as "Not working — synthesized 0 bytes" while a fallback voice still spoke
(looks broken yet audible). The factory now sanitises foreign values to the
ElevenLabs defaults. Mirrors the OpenRouter ``coerce_speech_model`` guard.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import jarvis.plugins.tts as tts_pkg
from jarvis.plugins.tts.elevenlabs_tts import (
    DEFAULT_MODEL,
    JARVIS_VOICE_DANIEL,
    coerce_elevenlabs_model,
)


# --- coerce_elevenlabs_model: foreign model ids fall back to the default ------


def test_cartesia_model_is_coerced_to_default() -> None:
    assert coerce_elevenlabs_model("sonic-2") == DEFAULT_MODEL


def test_gemini_model_is_coerced_to_default() -> None:
    assert coerce_elevenlabs_model("gemini-3.1-flash-tts-preview") == DEFAULT_MODEL


def test_openrouter_shaped_model_is_coerced_to_default() -> None:
    assert coerce_elevenlabs_model("google/gemini-3.1-flash-tts-preview") == DEFAULT_MODEL


def test_empty_and_none_model_use_default() -> None:
    assert coerce_elevenlabs_model("") == DEFAULT_MODEL
    assert coerce_elevenlabs_model(None) == DEFAULT_MODEL
    assert coerce_elevenlabs_model("   ") == DEFAULT_MODEL


def test_known_eleven_model_is_kept() -> None:
    assert coerce_elevenlabs_model("eleven_multilingual_v2") == "eleven_multilingual_v2"


def test_new_unlisted_eleven_model_is_trusted() -> None:
    # A NEW eleven_* model we don't list yet is trusted (prefix rule), not blocked.
    assert coerce_elevenlabs_model("eleven_flash_v9_future") == "eleven_flash_v9_future"


# --- factory build: a fully contaminated config yields a valid instance -------


def _cfg(**over: Any) -> SimpleNamespace:
    base = dict(
        provider="elevenlabs",
        model="",
        voice_de="",
        voice_en="",
        language_code="de-DE",
        stability=0.5,
        similarity_boost=0.75,
        style=0.0,
        speed=1.0,
        allow_sapi5_fallback=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_build_drops_cartesia_model_and_gemini_voice() -> None:
    """The exact live failure: model='sonic-2' (Cartesia), voice_de='Kore' (Gemini)."""
    inst = tts_pkg._build_provider(_cfg(model="sonic-2", voice_de="Kore", voice_en="Kore"), "elevenlabs")
    assert inst._model == DEFAULT_MODEL
    assert inst._model.startswith("eleven")
    # The foreign Gemini voice name must NOT survive as a bogus ElevenLabs id.
    assert inst._default_voice == JARVIS_VOICE_DANIEL
    assert inst._default_voice != "Kore"


def test_build_keeps_a_real_elevenlabs_voice_and_model() -> None:
    """A genuine ElevenLabs voice id (cryptic hash) + model is preserved as-is."""
    inst = tts_pkg._build_provider(
        _cfg(model="eleven_turbo_v2_5", voice_de="onwK4e9ZLuTAKqWW03F9"),
        "elevenlabs",
    )
    assert inst._model == "eleven_turbo_v2_5"
    assert inst._default_voice == "onwK4e9ZLuTAKqWW03F9"
