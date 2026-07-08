"""resolve_keyed_fallback: a plugin's internal fallback crosses to a family the
host actually has a key for (AP-22), never a keyless mute provider."""
from __future__ import annotations

import jarvis.core.config as cfg
import jarvis.plugins.tts as tts_pkg


def _keys(*env_names: str):
    have = set(env_names)

    def fake(candidates: tuple[tuple[str, str], ...]) -> str | None:
        for _keyring, env in candidates:
            if env in have:
                return "KEY"
        return None

    return fake


def test_crosses_to_a_keyed_family_excluding_self(monkeypatch):
    monkeypatch.setattr(cfg, "get_secret_any", _keys("GEMINI_API_KEY"))
    fb = tts_pkg.resolve_keyed_fallback("cartesia")
    assert fb is not None
    assert tts_pkg._canonical_tts_name(fb.name) == "gemini-flash-tts"


def test_none_when_only_the_excluded_family_has_a_key(monkeypatch):
    # Only Cartesia has a key, and Cartesia is excluded → no keyed fallback.
    monkeypatch.setattr(cfg, "get_secret_any", _keys("CARTESIA_API_KEY"))
    assert tts_pkg.resolve_keyed_fallback("cartesia") is None


def test_named_family_is_excluded_even_when_it_has_a_key(monkeypatch):
    # Inworld + Gemini keyed; excluding Inworld must pick Gemini, not Inworld.
    monkeypatch.setattr(cfg, "get_secret_any", _keys("INWORLD_API_KEY", "GEMINI_API_KEY"))
    fb = tts_pkg.resolve_keyed_fallback("inworld")
    assert fb is not None
    assert tts_pkg._canonical_tts_name(fb.name) == "gemini-flash-tts"


def test_no_key_anywhere_returns_none(monkeypatch):
    monkeypatch.setattr(cfg, "get_secret_any", _keys())
    assert tts_pkg.resolve_keyed_fallback("inworld") is None


def test_openrouter_is_last_resort(monkeypatch):
    # Only an OpenRouter key present → it IS chosen (last in the order, but the
    # only keyed family that isn't excluded).
    monkeypatch.setattr(cfg, "get_secret_any", _keys("OPENROUTER_API_KEY"))
    fb = tts_pkg.resolve_keyed_fallback("cartesia")
    assert fb is not None
    assert tts_pkg._canonical_tts_name(fb.name) == "openrouter"
