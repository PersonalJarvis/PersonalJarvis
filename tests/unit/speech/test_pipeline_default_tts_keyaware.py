"""The pipeline's default TTS (when none is supplied) is key-aware, not hard Gemini.

Regression guard: SpeechPipeline fell back to a bare GeminiFlashTTS when built with
tts=None, ignoring the user's actual key — so a single-key user without a Gemini
key would get MUTE spoken output, INCLUDING the honest "couldn't understand you"
readback. The default must route through the key-aware build_tts_from_config
(AP-22), degrading to Gemini only when there is no config or the factory fails.
"""
from __future__ import annotations

from types import SimpleNamespace

import jarvis.plugins.tts as tts_pkg
from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS
from jarvis.speech.pipeline import _default_tts_for_pipeline


def test_default_tts_uses_the_key_aware_factory(monkeypatch):
    sentinel = object()
    seen: dict[str, object] = {}

    def _fake_build(tts_cfg):
        seen["cfg"] = tts_cfg
        return sentinel

    monkeypatch.setattr(tts_pkg, "build_tts_from_config", _fake_build)
    cfg = SimpleNamespace(tts=SimpleNamespace(provider="gemini-flash-tts"))

    assert _default_tts_for_pipeline(cfg) is sentinel
    assert seen["cfg"] is cfg.tts


def test_default_tts_falls_back_to_gemini_without_config():
    assert isinstance(_default_tts_for_pipeline(None), GeminiFlashTTS)


def test_default_tts_falls_back_to_gemini_when_factory_fails(monkeypatch):
    def _boom(tts_cfg):
        raise RuntimeError("no provider buildable")

    monkeypatch.setattr(tts_pkg, "build_tts_from_config", _boom)
    cfg = SimpleNamespace(tts=SimpleNamespace(provider="gemini-flash-tts"))

    assert isinstance(_default_tts_for_pipeline(cfg), GeminiFlashTTS)
