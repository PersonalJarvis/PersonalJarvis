"""Factory wiring for Inworld + the canonical same-family fallback guard (AP-22)."""
from __future__ import annotations

from typing import Any

import jarvis.core.config as cfg
import jarvis.plugins.tts as tts_pkg
from jarvis.core.config import TTSConfig
from jarvis.plugins.tts.inworld_tts import InworldTTS


def _keys(*env_names: str):
    have = set(env_names)

    def fake(candidates: tuple[tuple[str, str], ...]) -> str | None:
        for _keyring, env in candidates:
            if env in have:
                return "KEY"
        return None

    return fake


def _cfg(provider: str, **over: Any) -> TTSConfig:
    # Real TTSConfig (extra="allow") so building any provider finds every field
    # it reads; `fallback` rides in as an extra key the factory reads via getattr.
    return TTSConfig(provider=provider, **over)


def test_inworld_aliases_canonicalize():
    for alias in ("inworld", "inworld-tts", "inworld-tts-2", "inworld_tts"):
        assert tts_pkg._canonical_tts_name(alias) == "inworld"


def test_factory_builds_inworld(monkeypatch):
    monkeypatch.setattr(cfg, "get_secret_any", _keys("INWORLD_API_KEY"))
    built = tts_pkg.build_tts_from_config(_cfg("inworld"))
    assert isinstance(built, InworldTTS)
    assert built.name == "inworld"


def test_inworld_leads_cross_family_order(monkeypatch):
    # A configured provider with no key, but only an Inworld key present, crosses
    # to Inworld (native premium first, per the new order).
    monkeypatch.setattr(cfg, "get_secret_any", _keys("INWORLD_API_KEY"))
    name, _view = tts_pkg._resolve_keyed_tts_provider("cartesia", _cfg("cartesia"))
    assert name == "inworld"


def test_canonical_same_family_guard_no_brick(monkeypatch):
    # provider="gemini" + fallback="gemini-flash-tts" resolve to the SAME family;
    # the factory must return a bare provider, NOT a FallbackTTS single-family brick.
    monkeypatch.setattr(cfg, "get_secret_any", _keys("GEMINI_API_KEY"))
    built = tts_pkg.build_tts_from_config(_cfg("gemini", fallback="gemini-flash-tts"))
    assert type(built).__name__ != "FallbackTTS"


def test_distinct_family_fallback_still_wraps(monkeypatch):
    # A genuinely different fallback family DOES wrap in FallbackTTS.
    monkeypatch.setattr(cfg, "get_secret_any", _keys("INWORLD_API_KEY", "CARTESIA_API_KEY"))
    built = tts_pkg.build_tts_from_config(_cfg("inworld", fallback="cartesia"))
    assert type(built).__name__ == "FallbackTTS"
