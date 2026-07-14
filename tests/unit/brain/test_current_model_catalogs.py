"""Current-model snapshot and cross-surface drift guards (2026-07-10)."""
from __future__ import annotations

from collections.abc import Iterable

from jarvis.brain.model_catalog import (
    CURATED_MODELS,
    REALTIME_MODELS,
    REALTIME_VOICES,
    STT_CATALOG,
    TTS_CATALOG,
    ModelInfo,
    catalog_spec,
    filter_brain_models,
    is_starred_model,
    parse_models_response,
)
from jarvis.plugins.tts import curated_catalog
from jarvis.plugins.tts.gemini_flash_tts import DEFAULT_VOICES as GEMINI_VOICES
from jarvis.plugins.tts.grok_voice_tts import DEFAULT_VOICES as XAI_VOICES


def _ids(models: Iterable[ModelInfo]) -> set[str]:
    return {model.id for model in models}


def test_openrouter_fallback_contains_the_complete_gpt_5_6_series() -> None:
    expected = {
        "openai/gpt-5.6-sol",
        "openai/gpt-5.6-sol-pro",
        "openai/gpt-5.6-terra",
        "openai/gpt-5.6-terra-pro",
        "openai/gpt-5.6-luna",
        "openai/gpt-5.6-luna-pro",
    }

    assert expected <= _ids(CURATED_MODELS["openrouter"])
    assert is_starred_model("openai/gpt-5.6-sol")


def test_live_openrouter_response_keeps_every_gpt_5_6_text_model() -> None:
    expected = {
        "openai/gpt-5.6-sol",
        "openai/gpt-5.6-sol-pro",
        "openai/gpt-5.6-terra",
        "openai/gpt-5.6-terra-pro",
        "openai/gpt-5.6-luna",
        "openai/gpt-5.6-luna-pro",
    }
    payload = {
        "data": [
            {
                "id": model_id,
                "name": model_id,
                "architecture": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                },
                "supported_parameters": ["tools"],
            }
            for model_id in expected
        ]
    }

    parsed = parse_models_response("openrouter", payload)

    assert _ids(filter_brain_models(parsed)) == expected
    assert all(model.input_modalities == ("text", "image") for model in parsed)


def test_direct_openai_and_codex_catalogs_offer_current_gpt_models() -> None:
    assert {"gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"} <= _ids(
        CURATED_MODELS["openai"]
    )
    assert {
        "gpt-5.5",
        "gpt-5.5-pro",
        "gpt-5.4",
        "gpt-5.4-pro",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
    } <= _ids(CURATED_MODELS["openai"])
    codex = catalog_spec("codex")
    assert codex is not None
    assert {
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
    } == _ids(codex.curated)
    assert not any(model_id.endswith(("-pro", "-codex")) for model_id in _ids(codex.curated))


def test_gemini_fallback_uses_current_non_media_models() -> None:
    ids = _ids(CURATED_MODELS["gemini"])

    assert {
        "gemini-3.5-flash",
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite",
    } <= ids
    assert "gemini-3-pro" not in ids
    assert "gemini-3-pro-preview" not in ids


def test_current_stt_and_cartesia_model_rosters() -> None:
    assert _ids(STT_CATALOG["groq-api"]) == {
        "whisper-large-v3",
        "whisper-large-v3-turbo",
    }
    assert _ids(STT_CATALOG["openai-api"]) == {
        "gpt-4o-transcribe",
        "gpt-4o-mini-transcribe",
        "gpt-4o-mini-transcribe-2025-12-15",
        "gpt-4o-transcribe-diarize",
        "whisper-1",
    }
    assert _ids(TTS_CATALOG["cartesia"][1]) == {
        "sonic-3.5",
        "sonic-3",
        "sonic-3-latest",
    }


def test_current_realtime_models_and_voices() -> None:
    assert {
        "gpt-realtime",
        "gpt-realtime-mini",
        "gpt-realtime-1.5",
        "gpt-realtime-2",
        "gpt-realtime-2.1",
        "gpt-realtime-2.1-mini",
    } == _ids(REALTIME_MODELS["openai-realtime"])
    assert _ids(REALTIME_VOICES["openai-realtime"]) == {
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "sage",
        "shimmer",
        "verse",
        "marin",
        "cedar",
    }
    assert {
        "gemini-3.1-flash-live-preview",
        "gemini-2.5-flash-native-audio-latest",
        "gemini-2.5-flash-native-audio-preview-12-2025",
    } == _ids(REALTIME_MODELS["gemini-live"])
    assert len(_ids(REALTIME_VOICES["gemini-live"])) == 30


def test_gemini_voice_roster_is_identical_across_runtime_and_pickers() -> None:
    picker = _ids(TTS_CATALOG["gemini-flash-tts"][1])
    realtime = _ids(REALTIME_VOICES["gemini-live"])
    allowed = {
        voice.id
        for voice in curated_catalog.allowed_voices(
            "gemini-flash-tts", "gemini-3.1-flash-tts-preview"
        )
    }

    assert len(GEMINI_VOICES) == 30
    assert set(GEMINI_VOICES) == picker == realtime == allowed


def test_xai_voice_roster_is_identical_across_runtime_and_picker() -> None:
    picker = _ids(TTS_CATALOG["grok-voice"][1])
    allowed = {
        voice.id
        for voice in curated_catalog.allowed_voices(
            "grok-voice", "grok-voice-tts-1.0"
        )
    }

    assert len(XAI_VOICES) == 26
    assert set(XAI_VOICES) == picker == allowed
