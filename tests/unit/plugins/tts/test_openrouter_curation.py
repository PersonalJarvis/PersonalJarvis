"""The OpenRouter pick list must show only allowlisted models (no slop), and the
active-provider catalog must offer Inworld (the new default)."""
from __future__ import annotations

from jarvis.brain.model_catalog import TTS_CATALOG
from jarvis.plugins.tts import curated_catalog as cc

_SLOP = {
    "hexgrad/kokoro-82m",
    "canopylabs/orpheus-3b-0.1-ft",
    "sesame/csm-1b",
    "zyphra/zonos-v0.1-transformer",
    "zyphra/zonos-v0.1-hybrid",
}
_KEEP = {
    "google/gemini-3.1-flash-tts-preview",
    "x-ai/grok-voice-tts-1.0",
    "microsoft/mai-voice-2",
    "mistralai/voxtral-mini-tts-2603",
}


def _model_ids(entry) -> set[str]:
    _selects, models = entry
    return {m.id for m in models}


def test_openrouter_picklist_has_no_slop():
    ids = _model_ids(TTS_CATALOG["openrouter-tts"])
    assert ids & _SLOP == set(), f"slop leaked into picker: {ids & _SLOP}"


def test_openrouter_picklist_keeps_the_four_vetted():
    ids = _model_ids(TTS_CATALOG["openrouter-tts"])
    assert _KEEP <= ids


def test_openrouter_picklist_matches_allowlist():
    ids = _model_ids(TTS_CATALOG["openrouter-tts"])
    # Every listed id must be allowed by the curated catalog (fail-closed).
    for mid in ids:
        assert cc.is_allowed("openrouter", mid), mid


def test_inworld_is_offered_in_the_catalog():
    assert "inworld" in TTS_CATALOG
    selects, models = TTS_CATALOG["inworld"]
    assert selects == "voice"
    ids = {m.id for m in models}
    assert {"Josef", "Diego"} <= ids


def test_allowed_openrouter_boundary_drops_slop_from_a_live_list():
    raw = [
        "google/gemini-3.1-flash-tts-preview",
        "hexgrad/kokoro-82m",
        "sesame/csm-1b",
        "x-ai/grok-voice-tts-1.0",
    ]
    assert cc.allowed_openrouter_model_ids(raw) == [
        "google/gemini-3.1-flash-tts-preview",
        "x-ai/grok-voice-tts-1.0",
    ]
