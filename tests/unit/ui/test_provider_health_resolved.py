"""The section-health tab reports the RESOLVED provider, not the dead default.

Regression guard for audit item A6: ``_active_stt`` / ``_active_tts`` used to return
the raw configured provider (a keyless shipped default), so a single-key user whose
voice actually works via a crossed-to family saw a false "no key set" dot pointing
at the wrong fix. They must now report the provider the runtime actually crossed to
— while leaving the configured value untouched when it has a key (maintainer path).
"""
from __future__ import annotations

from types import SimpleNamespace

import jarvis.core.config as cfg
import jarvis.plugins.stt as stt_pkg
from jarvis.core.config import ResolvedEndpoint
from jarvis.ui.web.provider_routes import _active_stt, _active_tts


def _request(stt_provider: str, tts_provider: str):
    state = SimpleNamespace(
        config=SimpleNamespace(
            stt=SimpleNamespace(provider=stt_provider),
            tts=SimpleNamespace(
                provider=tts_provider, use_vertex=False, voice_de="", voice_en=""
            ),
        )
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _only(family_key: str):
    """A get_secret_any double where ONLY ``family_key`` resolves to a key."""

    def _fake(candidates) -> str | None:
        keys = {c[0] for c in candidates}
        return "real-key" if family_key in keys else None

    return _fake


def _no_proxy(monkeypatch) -> None:
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda pid, **kw: ResolvedEndpoint(base_url=None, credential=None, via_proxy=False),
    )


def test_active_stt_reports_the_crossed_to_provider(monkeypatch):
    _no_proxy(monkeypatch)
    monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda name: object)
    # groq default has no key; the user's only key is an OpenRouter key.
    monkeypatch.setattr(cfg, "get_secret_any", _only("openrouter_api_key"))

    assert _active_stt(_request("groq-api", "gemini-flash-tts")) == "openrouter-stt"


def test_active_stt_keeps_configured_when_it_has_a_key(monkeypatch):
    _no_proxy(monkeypatch)
    monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda name: object)
    monkeypatch.setattr(cfg, "get_secret_any", _only("groq_api_key"))

    assert _active_stt(_request("groq-api", "gemini-flash-tts")) == "groq-api"


def test_active_tts_reports_the_crossed_to_provider(monkeypatch):
    # gemini default has no key; the user's only key is ElevenLabs.
    monkeypatch.setattr(cfg, "get_secret_any", _only("elevenlabs_api_key"))

    assert _active_tts(_request("groq-api", "gemini-flash-tts")) == "elevenlabs"


def test_active_tts_keeps_configured_when_it_has_a_key(monkeypatch):
    monkeypatch.setattr(cfg, "get_secret_any", _only("gemini_api_key"))

    assert _active_tts(_request("groq-api", "gemini-flash-tts")) == "gemini-flash-tts"
