"""Tests for the curated TTS catalog — the single source of truth that keeps
low-quality models out of the selectable surface (the hard allowlist).

Design: docs/superpowers/specs/2026-07-07-tts-quality-curation-design.md §3.1.
"""
from __future__ import annotations

from jarvis.plugins.tts import curated_catalog as cc


def test_keep_models_are_allowed():
    # The premium models the research verified as production-grade.
    assert cc.is_allowed("gemini-flash-tts", "gemini-3.1-flash-tts-preview")
    assert cc.is_allowed("cartesia", "sonic-3.5")
    assert cc.is_allowed("grok-voice", "grok-voice-tts-1.0")
    assert cc.is_allowed("elevenlabs", "eleven_flash_v2_5")


def test_inworld_is_allowed_top_tier_with_native_voices():
    # The new premium default (arena-#1 realtime, mid-2026).
    assert cc.is_allowed("inworld", "inworld-tts-2")
    entries = cc.allowed_models(family="inworld")
    assert entries and entries[0].quality_tier == "S"
    assert entries[0].latency_class == "realtime"
    ids = {v.id for v in entries[0].voices}
    # Native German + Spanish voices must be curated (masculine assistant tone).
    assert {"Josef", "Diego"} <= ids


def test_inworld_voice_language_filter():
    de = cc.allowed_voices("inworld", "inworld-tts-2", language="de")
    assert any(v.id == "Josef" for v in de)
    assert all(v.language in ("de", cc.MULTILINGUAL) for v in de)


def test_discard_openrouter_models_are_not_allowed():
    # The five open-source slop models: en-centric / beta / GPU-bound.
    for slop in (
        "hexgrad/kokoro-82m",
        "canopylabs/orpheus-3b-0.1-ft",
        "sesame/csm-1b",
        "zyphra/zonos-v0.1-transformer",
        "zyphra/zonos-v0.1-hybrid",
    ):
        assert not cc.is_allowed("openrouter", slop), slop


def test_openrouter_keep_models_are_allowed():
    # Only the four vetted OpenRouter speech models survive the filter.
    for keep in (
        "google/gemini-3.1-flash-tts-preview",
        "x-ai/grok-voice-tts-1.0",
        "microsoft/mai-voice-2",
        "mistralai/voxtral-mini-tts-2603",
    ):
        assert cc.is_allowed("openrouter", keep), keep


def test_allowed_openrouter_model_ids_filters_a_raw_catalog():
    raw = [
        "google/gemini-3.1-flash-tts-preview",
        "hexgrad/kokoro-82m",
        "x-ai/grok-voice-tts-1.0",
        "sesame/csm-1b",
        "zyphra/zonos-v0.1-hybrid",
    ]
    kept = cc.allowed_openrouter_model_ids(raw)
    assert kept == [
        "google/gemini-3.1-flash-tts-preview",
        "x-ai/grok-voice-tts-1.0",
    ]


def test_allowed_models_can_filter_by_family():
    ids = {m.model_id for m in cc.allowed_models(family="cartesia")}
    assert "sonic-3.5" in ids
    # A different family's model must not leak into a family-scoped query.
    assert "gemini-3.1-flash-tts-preview" not in ids


def test_allowed_models_can_filter_by_language():
    # Every S/A model must cover the three first-class languages.
    for lang in ("de", "en", "es"):
        models = cc.allowed_models(language=lang)
        assert models, f"no allowed model advertises {lang}"
        assert all(lang in m.languages or "multi" in m.languages for m in models)


def test_unknown_model_is_not_allowed():
    # Fail-closed: an id nobody vetted is never selectable.
    assert not cc.is_allowed("openrouter", "some/unvetted-model-9000")
    assert not cc.is_allowed("gemini-flash-tts", "gemini-99-ultra-unreleased")


def test_quality_tiers_are_s_or_a():
    for m in cc.allowed_models():
        assert m.quality_tier in ("S", "A"), (m.family, m.model_id)


def test_every_allowed_model_declares_streaming_and_latency_class():
    for m in cc.allowed_models():
        assert isinstance(m.streaming, bool)
        assert m.latency_class in ("realtime", "standard", "batch")
