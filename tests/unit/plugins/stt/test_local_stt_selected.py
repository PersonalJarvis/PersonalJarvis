"""The on-device STT provider as a DELIBERATE user choice, not just a fallback.

Two directions have to hold at once, and they pull against each other:

* a user who picked local Whisper gets local Whisper — with the checkpoint the
  config names, not the engine's own default;
* a user whose host cannot run it (fresh machine, rebuilt venv, base install
  carrying an old config) does NOT get a provider that raises on the first
  utterance. That reads as "voice input is broken" with no cause, so the factory
  crosses to a cloud family the host actually holds a key for (AP-22).
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


def _no_proxy(monkeypatch) -> None:
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda pid, **kw: ResolvedEndpoint(base_url=None, credential=None, via_proxy=False),
    )
    monkeypatch.setattr(fwhisper, "FasterWhisperProvider", _FakeLocalSTT)


def test_selected_local_provider_is_built_locally(monkeypatch):
    """Picking it must run it here — even with a perfectly good cloud key present."""
    _no_proxy(monkeypatch)
    monkeypatch.setattr(stt_pkg, "_faster_whisper_installed", lambda: True)
    monkeypatch.setattr(cfg, "get_secret_any", lambda candidates: "gsk-real-key")

    provider = stt_pkg.build_stt_from_config(
        STTConfig(provider="faster-whisper", model="large-v3")
    )

    assert isinstance(provider, _FakeLocalSTT)
    assert provider.kwargs["model"] == "large-v3", (
        "The local provider must load the checkpoint the config names — running a "
        "different Whisper size than the user selected is the silent-wrong-model bug."
    )


def test_selected_local_provider_crosses_over_when_the_engine_is_absent(monkeypatch):
    """No engine on this host -> use a cloud family the user has a key for."""
    _no_proxy(monkeypatch)
    monkeypatch.setattr(stt_pkg, "_faster_whisper_installed", lambda: False)
    monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda name: _FakeCloudSTT)
    monkeypatch.setattr(cfg, "get_secret_any", lambda candidates: "gsk-real-key")

    provider = stt_pkg.build_stt_from_config(STTConfig(provider="faster-whisper"))

    assert isinstance(provider, _FakeCloudSTT), (
        "With the local engine missing, the factory must cross to a keyed cloud "
        "family instead of building a local provider that dies on first use."
    )


def test_absent_engine_and_no_cloud_key_keeps_the_honest_local_path(monkeypatch):
    """With nothing to cross to, the local path stands and the dead-end is logged.

    Inventing a provider here would be worse than the honest failure: the user
    has neither an engine nor a key, and only a truthful log line points at the
    fix.
    """
    _no_proxy(monkeypatch)
    monkeypatch.setattr(stt_pkg, "_faster_whisper_installed", lambda: False)
    monkeypatch.setattr(cfg, "get_secret_any", lambda candidates: None)

    provider = stt_pkg.build_stt_from_config(STTConfig(provider="faster-whisper"))

    assert isinstance(provider, _FakeLocalSTT)


def test_local_provider_is_registered_as_an_entry_point():
    """The runtime resolves it the same way it resolves every cloud provider.

    Without the entry-point the provider exists only as a hard-coded fallback
    branch, and nothing else (the fallback chain, the provider list) can see it.
    """
    assert stt_pkg._load_provider_class("faster-whisper") is not None
