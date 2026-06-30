"""Key-aware STT fallback: a cloud STT with no usable credential must fall back
to the local, key-free faster-whisper instead of being constructed and then
RuntimeError-ing on every utterance.

Open-source resilience (AP-22): the shipped default STT is the cloud `groq-api`,
but jarvis.toml is gitignored, so a fresh downloader whose single key is for any
OTHER provider (OpenAI / OpenRouter / Claude / Gemini) would otherwise get a Groq
instance that raises on the first transcription — bricking ALL voice input. The
factory must consult "is there actually a key?" and cross over to local Whisper
when there is not.
"""
from __future__ import annotations

from typing import Any

import jarvis.core.config as cfg
import jarvis.plugins.stt as stt_pkg
import jarvis.plugins.stt.fwhisper as fwhisper
from jarvis.core.config import ResolvedEndpoint, STTConfig


class _FakeCloudSTT:
    name = "groq-api"

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeLocalSTT:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _direct_mode(monkeypatch) -> None:
    """No team proxy: the factory must rely on the user's own key resolution."""
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda pid, **kw: ResolvedEndpoint(base_url=None, credential=None, via_proxy=False),
    )
    monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda name: _FakeCloudSTT)
    monkeypatch.setattr(fwhisper, "FasterWhisperProvider", _FakeLocalSTT)


def test_cloud_stt_without_key_falls_back_to_local(monkeypatch):
    _direct_mode(monkeypatch)
    # The user has NO Groq credential anywhere.
    monkeypatch.setattr(cfg, "get_secret_any", lambda candidates: None)

    provider = stt_pkg.build_stt_from_config(STTConfig(provider="groq-api"))

    assert isinstance(provider, _FakeLocalSTT), (
        "A cloud STT with no usable key must fall back to local faster-whisper, "
        "not be constructed and then brick on the first utterance."
    )


def test_cloud_stt_with_key_is_still_used(monkeypatch):
    _direct_mode(monkeypatch)
    # The user DOES have a Groq credential — the cloud provider must be used.
    monkeypatch.setattr(cfg, "get_secret_any", lambda candidates: "gsk-real-key")

    provider = stt_pkg.build_stt_from_config(STTConfig(provider="groq-api"))

    assert isinstance(provider, _FakeCloudSTT), (
        "When the cloud STT's key IS present, the factory must use the cloud "
        "provider, not needlessly fall back to local."
    )
