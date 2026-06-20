"""W2: the STT factory routes the Groq cloud STT through the team proxy.

In team mode the Groq transcription endpoint becomes ``{proxy}/p/groq-api/
audio/transcriptions`` and the per-user token is injected as the api_key, so a
client never holds the real Groq key. Direct mode is untouched.
"""
from __future__ import annotations

from typing import Any

import jarvis.core.config as cfg
import jarvis.plugins.stt as stt_pkg
from jarvis.core.config import ResolvedEndpoint, STTConfig


class _FakeGroq:
    name = "groq-api"
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeGroq.last_kwargs = kwargs


def _patch_groq_class(monkeypatch) -> None:
    monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda name: _FakeGroq)


def test_groq_stt_routed_through_proxy(monkeypatch):
    _patch_groq_class(monkeypatch)
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda pid, **kw: ResolvedEndpoint(
            base_url="https://keys.acme.dev/p/groq-api",
            credential="tok-123",
            via_proxy=True,
        ),
    )
    stt_pkg.build_stt_from_config(STTConfig(provider="groq-api"))
    assert (
        _FakeGroq.last_kwargs["endpoint"]
        == "https://keys.acme.dev/p/groq-api/audio/transcriptions"
    )
    assert _FakeGroq.last_kwargs["api_key"] == "tok-123"


def test_groq_stt_direct_mode_untouched(monkeypatch):
    _patch_groq_class(monkeypatch)
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda pid, **kw: ResolvedEndpoint(base_url=None, credential=None, via_proxy=False),
    )
    stt_pkg.build_stt_from_config(STTConfig(provider="groq-api"))
    # Direct mode: factory injects neither endpoint nor api_key — the provider
    # falls back to its own DEFAULT_ENDPOINT + key resolution.
    assert "endpoint" not in _FakeGroq.last_kwargs
    assert "api_key" not in _FakeGroq.last_kwargs
