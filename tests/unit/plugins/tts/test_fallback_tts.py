"""Tests for the provider-level TTS fallback (``FallbackTTS`` + factory wiring).

Background (2026-05-31): "Jarvis hears + thinks but never answers." The primary
TTS (gemini-flash-tts via Vertex) raised inside ``_ensure_client`` on every
sentence because the project was empty — and nothing fell back, so the user got
total silence. Root cause #2: ``[tts].fallback = "grok-voice"`` was DEAD config,
read by no one. ``GrokVoiceTTS`` had its own internal cross-provider chain, but
``GeminiFlashTTS`` (the primary) had none, and the factory never wired the
``fallback`` field.

These tests lock in: (a) the factory wraps primary+fallback when configured and
returns the raw provider otherwise, and (b) the wrapper switches to the fallback
exactly when the primary fails to produce audio — without duplicating audio.
"""
from __future__ import annotations

import pytest

from jarvis.plugins.tts import build_tts_from_config
from jarvis.plugins.tts.fallback_tts import FallbackTTS
from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS
from jarvis.plugins.tts.grok_voice_tts import GrokVoiceTTS


# --- Fakes ------------------------------------------------------------------

class _FakeTTS:
    """Configurable stand-in TTS provider."""

    supports_streaming = True

    def __init__(
        self, name, *, chunks=1, raise_in_ensure=False,
        raise_before=False, raise_after_n=None,
    ):
        self.name = name
        self._chunks = chunks
        self._raise_in_ensure = raise_in_ensure
        self._raise_before = raise_before
        self._raise_after_n = raise_after_n
        self.ensure_calls = 0
        self.synth_calls: list[tuple] = []

    def _ensure_client(self):
        self.ensure_calls += 1
        if self._raise_in_ensure:
            raise RuntimeError(f"{self.name} ensure boom")

    async def synthesize(self, text, voice=None, language_code=None):
        self.synth_calls.append((text, voice, language_code))
        if self._raise_before:
            raise RuntimeError(f"{self.name} synth boom (before any chunk)")
        for i in range(self._chunks):
            if self._raise_after_n is not None and i >= self._raise_after_n:
                raise RuntimeError(f"{self.name} synth boom (mid-stream)")
            yield f"{self.name}-{i}"


async def _drain(tts, text="Hallo Welt.", voice=None, language_code="de-DE"):
    return [c async for c in tts.synthesize(text, voice=voice, language_code=language_code)]


# --- Wrapper behaviour ------------------------------------------------------

@pytest.mark.asyncio
async def test_primary_healthy_never_touches_fallback():
    primary = _FakeTTS("primary", chunks=2)
    fallback = _FakeTTS("fallback", chunks=5)
    out = await _drain(FallbackTTS(primary, fallback))
    assert out == ["primary-0", "primary-1"]
    assert fallback.synth_calls == []  # fallback untouched


@pytest.mark.asyncio
async def test_falls_back_when_primary_raises_before_audio():
    primary = _FakeTTS("primary", raise_before=True)
    fallback = _FakeTTS("fallback", chunks=2)
    out = await _drain(FallbackTTS(primary, fallback))
    assert out == ["fallback-0", "fallback-1"]
    assert len(fallback.synth_calls) == 1


@pytest.mark.asyncio
async def test_falls_back_when_primary_yields_nothing():
    primary = _FakeTTS("primary", chunks=0)  # empty stream, no exception
    fallback = _FakeTTS("fallback", chunks=1)
    out = await _drain(FallbackTTS(primary, fallback))
    assert out == ["fallback-0"]


@pytest.mark.asyncio
async def test_midstream_failure_reraises_and_does_not_duplicate():
    # Primary yields one chunk then dies — falling back would replay the start.
    primary = _FakeTTS("primary", chunks=3, raise_after_n=1)
    fallback = _FakeTTS("fallback", chunks=2)
    wrapper = FallbackTTS(primary, fallback)
    with pytest.raises(RuntimeError, match="mid-stream"):
        await _drain(wrapper)
    assert fallback.synth_calls == []  # no fallback after partial audio


@pytest.mark.asyncio
async def test_empty_text_yields_nothing_and_skips_fallback():
    primary = _FakeTTS("primary", chunks=2)
    fallback = _FakeTTS("fallback", chunks=2)
    out = await _drain(FallbackTTS(primary, fallback), text="   ")
    assert out == []
    assert primary.synth_calls == []
    assert fallback.synth_calls == []


@pytest.mark.asyncio
async def test_fallback_is_not_given_the_primary_voice():
    # Primary voice "Charon" is invalid for a different provider — the fallback
    # must receive voice=None so it resolves its own default.
    primary = _FakeTTS("primary", raise_before=True)
    fallback = _FakeTTS("fallback", chunks=1)
    await _drain(FallbackTTS(primary, fallback), voice="Charon")
    assert fallback.synth_calls[0][1] is None  # voice arg forwarded as None


def test_ensure_client_never_raises_and_warms_both():
    primary = _FakeTTS("primary", raise_in_ensure=True)
    fallback = _FakeTTS("fallback")
    wrapper = FallbackTTS(primary, fallback)
    wrapper._ensure_client()  # must not raise despite primary boom
    assert primary.ensure_calls == 1
    assert fallback.ensure_calls == 1


def test_wrapper_surfaces_primary_identity():
    primary = _FakeTTS("primary")
    wrapper = FallbackTTS(primary, _FakeTTS("fallback"))
    assert wrapper.name == "primary"
    assert wrapper.supports_streaming is True


# --- Factory wiring ---------------------------------------------------------

class _Cfg:
    """Minimal TTSConfig stand-in with a configured fallback."""
    provider = "gemini-flash-tts"
    fallback = "grok-voice"
    model = "gemini-3.1-flash-tts-preview"
    voice_de = "Charon"
    voice_en = "Charon"
    language_code = "de-DE"
    style_prompt = ""
    allow_sapi5_fallback = False
    chunk_by_sentence = False
    seed = 7
    temperature = 0.7
    use_vertex = False
    vertex_project = None
    vertex_location = "us-central1"
    service_account_path = None
    speed = 1.0
    stability = 0.5
    similarity_boost = 0.75
    style = 0.0


def test_factory_wraps_primary_and_fallback_when_configured():
    tts = build_tts_from_config(_Cfg())
    assert isinstance(tts, FallbackTTS)
    assert isinstance(tts.primary, GeminiFlashTTS)
    assert isinstance(tts.fallback, GrokVoiceTTS)


def test_factory_returns_raw_provider_without_fallback():
    class _NoFb(_Cfg):
        fallback = ""
    tts = build_tts_from_config(_NoFb())
    assert isinstance(tts, GeminiFlashTTS)  # no wrapper -> isinstance still holds


def test_factory_does_not_wrap_when_fallback_equals_primary():
    class _Same(_Cfg):
        fallback = "gemini-flash-tts"
    tts = build_tts_from_config(_Same())
    assert isinstance(tts, GeminiFlashTTS)


def test_factory_degrades_to_primary_when_fallback_unbuildable(monkeypatch):
    import jarvis.plugins.tts as factory
    orig = factory._build_provider

    def _boom(cfg, provider):
        if provider == "grok-voice":
            raise RuntimeError("simulated plugin import failure")
        return orig(cfg, provider)

    monkeypatch.setattr(factory, "_build_provider", _boom)
    tts = factory.build_tts_from_config(_Cfg())
    assert isinstance(tts, GeminiFlashTTS)  # degraded, not crashed
