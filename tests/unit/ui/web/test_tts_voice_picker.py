"""The TTS voice picker is cross-provider (not OpenRouter-only) and allowlisted.

Tests the pure ``_tts_voice_entries`` helper directly (the FastAPI routes that
wrap it are covered live by the Chrome checkup; the route TestClient is skipped
on this stack by a pre-existing fastapi/starlette version drift).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from jarvis.ui.web.provider_routes import _tts_voice_entries


def test_inworld_voices_served():
    entries, model_id, default = _tts_voice_entries("inworld", "")
    ids = {e["id"] for e in entries}
    assert model_id == "inworld-tts-2"
    assert default == "Josef"
    assert {"Josef", "Diego"} <= ids


def test_native_families_each_serve_voices():
    for prov, expect in (
        ("elevenlabs", "onwK4e9ZLuTAKqWW03F9"),
        ("grok-voice", "leo"),
        ("gemini", "Charon"),
    ):
        entries, _model, default = _tts_voice_entries(prov, "")
        assert default == expect, prov
        assert entries


def test_cartesia_falls_back_to_model_level_catalog():
    # Cartesia curates no per-voice list (model-level pick); it must still return
    # its catalog entries, not an empty list or a 400.
    entries, model_id, _default = _tts_voice_entries("cartesia", "")
    assert model_id == "sonic-3.5"
    assert {e["id"] for e in entries} & {"sonic-3.5", "sonic-2", "sonic-turbo"}


def test_openrouter_serves_per_model_voices():
    entries, model_id, default = _tts_voice_entries(
        "openrouter-tts", "google/gemini-3.1-flash-tts-preview"
    )
    assert model_id == "google/gemini-3.1-flash-tts-preview"
    assert default
    assert entries


def test_openrouter_slop_model_is_rejected():
    with pytest.raises(HTTPException) as exc:
        _tts_voice_entries("openrouter-tts", "hexgrad/kokoro-82m")
    assert exc.value.status_code == 400


def test_unknown_provider_is_rejected():
    with pytest.raises(HTTPException) as exc:
        _tts_voice_entries("nonexistent-provider", "")
    assert exc.value.status_code == 400
