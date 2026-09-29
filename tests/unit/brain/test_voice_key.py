"""The realtime voice key is reserved for the voice call (mandate 2026-09-29)."""

from __future__ import annotations

from types import SimpleNamespace

from jarvis.brain.voice_key import bills_voice_key, voice_key_slots


def _cfg(mode: str, realtime_provider: str) -> SimpleNamespace:
    return SimpleNamespace(
        voice=SimpleNamespace(mode=mode),
        brain=SimpleNamespace(realtime=SimpleNamespace(provider=realtime_provider)),
    )


def test_openai_live_reserves_the_openai_key() -> None:
    cfg = _cfg("realtime", "openai-live")

    assert "openai_api_key" in voice_key_slots(cfg)
    assert bills_voice_key(cfg, "openai")
    assert not bills_voice_key(cfg, "grok")
    assert not bills_voice_key(cfg, "claude-cli")


def test_pipeline_voice_reserves_nothing() -> None:
    cfg = _cfg("pipeline", "openai-live")

    assert voice_key_slots(cfg) == frozenset()
    assert not bills_voice_key(cfg, "openai")


def test_unknown_or_missing_realtime_provider_reserves_nothing() -> None:
    assert voice_key_slots(_cfg("realtime", "")) == frozenset()
    assert voice_key_slots(_cfg("realtime", "no-such-voice-plugin")) == frozenset()
    assert not bills_voice_key(None, "openai")
