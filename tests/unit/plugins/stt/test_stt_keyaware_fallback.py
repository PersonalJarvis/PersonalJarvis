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


def _only(family_key: str):
    """A get_secret_any double where ONLY ``family_key`` resolves to a key."""

    def _fake(candidates) -> str | None:
        keys = {c[0] for c in candidates}
        return "real-key" if family_key in keys else None

    return _fake


def test_resolver_crosses_to_openrouter_when_only_it_has_a_key(monkeypatch):
    """Configured groq with no groq key, but an OpenRouter key present -> cross.

    This is THE common fresh-download case: the single key is an OpenRouter key
    (a gateway shared with the brain), and openrouter-stt reuses it. The resolver
    must pick it instead of dead-ending on local whisper (open-source AP-22).
    """
    monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda name: _FakeCloudSTT)
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda pid, **kw: ResolvedEndpoint(base_url=None, credential=None, via_proxy=False),
    )
    monkeypatch.setattr(cfg, "get_secret_any", _only("openrouter_api_key"))

    assert stt_pkg._resolve_keyed_stt_provider("groq-api") == "openrouter-stt"


def test_resolver_keeps_provider_when_no_cloud_family_has_a_key(monkeypatch):
    """No cloud STT family keyed -> keep configured name (caller drops to local)."""
    monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda name: _FakeCloudSTT)
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda pid, **kw: ResolvedEndpoint(base_url=None, credential=None, via_proxy=False),
    )
    monkeypatch.setattr(cfg, "get_secret_any", lambda candidates: None)

    assert stt_pkg._resolve_keyed_stt_provider("groq-api") == "groq-api"


def test_resolver_skips_a_keyed_but_unregistered_family(monkeypatch):
    """A family with a key but NO entry-point must be skipped, not promised.

    openai has a candidate key slot but ships no jarvis.stt entry-point today, so
    an openai-only user still falls through to local whisper rather than getting a
    provider that cannot be built.
    """
    # Only openrouter-stt resolves to a class; every other name is "unregistered".
    monkeypatch.setattr(
        stt_pkg,
        "_load_provider_class",
        lambda name: _FakeCloudSTT if name == "openrouter-stt" else None,
    )
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda pid, **kw: ResolvedEndpoint(base_url=None, credential=None, via_proxy=False),
    )
    # Only an openai key is present — but openai is unregistered here.
    monkeypatch.setattr(cfg, "get_secret_any", _only("openai_api_key"))

    assert stt_pkg._resolve_keyed_stt_provider("groq-api") == "groq-api"


def test_cross_family_build_uses_cloud_not_local(monkeypatch):
    """End to end: groq default + only an OpenRouter key -> cloud STT, not local."""
    loaded: dict[str, str] = {}

    def _fake_load(name: str):
        loaded["name"] = name
        return _FakeCloudSTT

    monkeypatch.setattr(stt_pkg, "_load_provider_class", _fake_load)
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda pid, **kw: ResolvedEndpoint(base_url=None, credential=None, via_proxy=False),
    )
    monkeypatch.setattr(fwhisper, "FasterWhisperProvider", _FakeLocalSTT)
    monkeypatch.setattr(cfg, "get_secret_any", _only("openrouter_api_key"))

    provider = stt_pkg.build_stt_from_config(STTConfig(provider="groq-api"))

    assert isinstance(provider, _FakeCloudSTT), (
        "A single-key OpenRouter user must get cloud STT via the cross-family "
        "resolver, not dead-end on local faster-whisper the base install lacks."
    )
    assert loaded["name"] == "openrouter-stt"
