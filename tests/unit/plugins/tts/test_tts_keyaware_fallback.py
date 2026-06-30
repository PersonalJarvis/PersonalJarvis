"""Key-aware TTS fallback: a configured TTS provider with no usable key must
cross to whatever TTS family the user DOES have a key for, instead of building a
keyless provider that goes silently mute.

Open-source resilience (AP-22): the shipped default TTS is ``gemini-flash-tts``,
but jarvis.toml is gitignored, so a fresh downloader whose single TTS key is for
ElevenLabs / Cartesia / Grok (or who has no Gemini key) would otherwise get a
Gemini instance that produces no audio — leaving Jarvis mute. The factory must
consult "is there actually a key?" and cross families, mirroring the STT path.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import jarvis.core.config as cfg
import jarvis.plugins.tts as tts_pkg


def _keys(*env_names: str):
    """Fake ``get_secret_any`` that returns a key only for the named env vars."""
    have = set(env_names)

    def fake(candidates: tuple[tuple[str, str], ...]) -> str | None:
        for _keyring, env in candidates:
            if env in have:
                return "KEY"
        return None

    return fake


def _cfg(provider: str, **over: Any) -> SimpleNamespace:
    base = dict(provider=provider, voice_de="Charon", voice_en="Charon", use_vertex=False)
    base.update(over)
    return SimpleNamespace(**base)


# --- The mute scenario: a single non-Gemini key must cross, not go silent ------


def test_gemini_default_with_only_elevenlabs_key_crosses(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "get_secret_any", _keys("ELEVENLABS_API_KEY"))
    name, view = tts_pkg._resolve_keyed_tts_provider("gemini-flash-tts", _cfg("gemini-flash-tts"))
    assert name == "elevenlabs"
    # The Gemini default voice must NOT be carried into ElevenLabs as a bogus id.
    assert view.voice_de == ""
    assert view.voice_en == ""


def test_elevenlabs_configured_with_only_gemini_key_crosses(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "get_secret_any", _keys("GEMINI_API_KEY"))
    name, view = tts_pkg._resolve_keyed_tts_provider("elevenlabs", _cfg("elevenlabs", voice_de="x", voice_en="x"))
    assert name == "gemini-flash-tts"


def test_only_cartesia_key_crosses_to_cartesia(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "get_secret_any", _keys("CARTESIA_API_KEY"))
    name, _view = tts_pkg._resolve_keyed_tts_provider("gemini-flash-tts", _cfg("gemini-flash-tts"))
    assert name == "cartesia"


# --- The maintainer path is untouched -----------------------------------------


def test_configured_provider_with_its_key_is_kept(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "get_secret_any", _keys("GEMINI_API_KEY"))
    c = _cfg("gemini-flash-tts")
    name, view = tts_pkg._resolve_keyed_tts_provider("gemini-flash-tts", c)
    assert name == "gemini-flash-tts"
    assert view is c  # unchanged config, no voice rewrite


def test_gemini_vertex_counts_as_credential(monkeypatch) -> None:
    """Vertex AI uses a service account, not an API key — no cross-over."""
    monkeypatch.setattr(cfg, "get_secret_any", _keys())  # no API keys at all
    name, _view = tts_pkg._resolve_keyed_tts_provider(
        "gemini-flash-tts", _cfg("gemini-flash-tts", use_vertex=True)
    )
    assert name == "gemini-flash-tts"


# --- Honest degrade when NOTHING is reachable (never a silent swap) ------------


def test_no_key_anywhere_keeps_configured_provider(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "get_secret_any", _keys())  # no key for any family
    name, view = tts_pkg._resolve_keyed_tts_provider("gemini-flash-tts", _cfg("gemini-flash-tts"))
    assert name == "gemini-flash-tts"  # kept; degrades to SAPI5/honest mute, not a swap


# --- Cross-family target keeps a voice that DOES belong to it ------------------


def test_cross_to_grok_keeps_grok_voice(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "get_secret_any", _keys("XAI_API_KEY"))
    name, view = tts_pkg._resolve_keyed_tts_provider(
        "gemini-flash-tts", _cfg("gemini-flash-tts", voice_de="leo", voice_en="leo")
    )
    assert name == "grok-voice"
    assert view.voice_de == "leo"  # a real Grok voice survives the cross-over


# --- End-to-end wiring: the factory builds the crossed-to provider -------------


def test_factory_builds_crossed_provider(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "get_secret_any", _keys("ELEVENLABS_API_KEY"))
    captured: dict[str, Any] = {}

    def _fake_build(tts_cfg: Any, provider: str) -> Any:
        captured["provider"] = provider
        captured["voice_de"] = getattr(tts_cfg, "voice_de", None)
        return SimpleNamespace(kind="built", provider=provider)

    monkeypatch.setattr(tts_pkg, "_build_provider", _fake_build)
    built = tts_pkg.build_tts_from_config(_cfg("gemini-flash-tts"))
    assert built.provider == "elevenlabs"
    assert captured["provider"] == "elevenlabs"
    assert captured["voice_de"] == ""  # foreign Gemini voice was blanked
