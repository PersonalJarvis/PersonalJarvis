"""Tests fuer jarvis.missions.worker_runtime.provider_map.

Quelle der Wahrheit: docs/openclaw-bridge.md AD-6 Amendment-Tabelle.
Bei Aenderung der Tabelle bitte BEIDES anpassen — Doku ist verbindlich.
"""
from __future__ import annotations

import pytest

from jarvis.missions.worker_runtime.provider_map import (
    JARVIS_TO_OPENCLAW,
    MAPPINGS,
    OPENCLAW_TO_JARVIS,
    ProviderMapping,
    UnknownJarvisProviderError,
    UnknownOpenclawProviderError,
    env_vars_for,
    to_jarvis_slug,
    to_provider_slug,
    validate_configured_providers,
)


# --- Forward-Mapping (jarvis -> openclaw) ---


@pytest.mark.parametrize(
    "jarvis_slug,openclaw_slug",
    [
        ("gemini", "google"),
        # 2026-05-17 — claude-api now routes through OpenClaw's claude-cli
        # backend (OAuth, Claude Max subscription) instead of `anthropic`
        # (paid Messages API with extra-usage requirement). See
        # provider_map.MAPPINGS docstring + docs/openclaw-bridge.md AD-6.
        ("claude-api", "claude-cli"),
        ("openai", "openai"),
        ("openrouter", "openrouter"),
        ("grok", "xai"),
    ],
)
def test_to_provider_slug_known_providers(jarvis_slug: str, openclaw_slug: str) -> None:
    """Alle 5 dokumentierten Provider mappen wie in AD-6 Amendment-Tabelle."""
    assert to_provider_slug(jarvis_slug) == openclaw_slug


def test_to_provider_slug_unknown_raises() -> None:
    with pytest.raises(UnknownJarvisProviderError) as exc_info:
        to_provider_slug("ollama-local")
    msg = str(exc_info.value)
    assert "ollama-local" in msg
    assert "claude-api" in msg  # Hinweis auf bekannte Mappings
    assert "MAPPINGS" in msg  # Hinweis wo erweitern


def test_to_provider_slug_empty_string_raises() -> None:
    with pytest.raises(UnknownJarvisProviderError):
        to_provider_slug("")


def test_to_provider_slug_case_sensitive() -> None:
    """Slugs sind lowercase — Case-Mismatch ist kein Auto-Match."""
    with pytest.raises(UnknownJarvisProviderError):
        to_provider_slug("Gemini")


# --- Reverse-Mapping (openclaw -> jarvis) ---


@pytest.mark.parametrize(
    "openclaw_slug,jarvis_slug",
    [
        ("google", "gemini"),
        ("claude-cli", "claude-api"),
        ("openai", "openai"),
        ("openrouter", "openrouter"),
        ("xai", "grok"),
    ],
)
def test_to_jarvis_slug_round_trip(openclaw_slug: str, jarvis_slug: str) -> None:
    assert to_jarvis_slug(openclaw_slug) == jarvis_slug


def test_to_jarvis_slug_unknown_raises() -> None:
    with pytest.raises(UnknownOpenclawProviderError) as exc_info:
        to_jarvis_slug("groq")  # OpenClaw kennt das, Personal-Jarvis nicht
    assert "groq" in str(exc_info.value)


def test_round_trip_jarvis_openclaw_jarvis() -> None:
    for jarvis_slug in JARVIS_TO_OPENCLAW:
        assert to_jarvis_slug(to_provider_slug(jarvis_slug)) == jarvis_slug


# --- ENV-Var-Mapping ---


@pytest.mark.parametrize(
    "jarvis_slug,expected_env",
    [
        ("gemini", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
        ("claude-api", ("ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY")),
        ("openai", ("OPENAI_API_KEY",)),
        ("openrouter", ("OPENROUTER_API_KEY",)),
        ("grok", ("XAI_API_KEY", "GROK_API_KEY")),
    ],
)
def test_env_vars_for_known_providers(
    jarvis_slug: str, expected_env: tuple[str, ...]
) -> None:
    """ENV-Var-Set matcht AD-6 Amendment, Reihenfolge: primary, fallback."""
    assert env_vars_for(jarvis_slug) == expected_env


def test_env_vars_for_unknown_raises() -> None:
    with pytest.raises(UnknownJarvisProviderError):
        env_vars_for("ollama-local")


def test_env_vars_primary_always_first() -> None:
    """Bridge soll primary-ENV zuerst setzen — beide gesetzt ist robuster."""
    primary, *_ = env_vars_for("gemini")
    assert primary == "GEMINI_API_KEY"


# --- Validation-Helper ---


def test_validate_configured_providers_all_mapped_returns_empty() -> None:
    configured = ["gemini", "claude-api", "openai", "grok"]
    assert validate_configured_providers(configured) == []


def test_validate_configured_providers_unmapped_listed() -> None:
    configured = ["gemini", "ollama-local", "openai", "codex"]
    unmapped = validate_configured_providers(configured)
    assert unmapped == ["codex", "ollama-local"]  # alphabetisch sortiert


def test_validate_configured_providers_empty_input() -> None:
    assert validate_configured_providers([]) == []


def test_validate_configured_providers_consumes_iterator() -> None:
    """Iterable-Argument darf auch ein Generator sein."""

    def gen() -> object:
        yield "gemini"
        yield "future-provider"

    assert validate_configured_providers(gen()) == ["future-provider"]


def test_validate_configured_providers_order_stable() -> None:
    """Ergebnis ist alphabetisch sortiert, unabhaengig von Input-Reihenfolge."""
    a = validate_configured_providers(["zzz", "aaa", "mmm"])
    b = validate_configured_providers(["aaa", "mmm", "zzz"])
    assert a == b == ["aaa", "mmm", "zzz"]


# --- Tabelle/Daten-Konsistenz ---


def test_mappings_are_unique_in_both_directions() -> None:
    """Kein doppelter jarvis-Slug, kein doppelter openclaw-Slug."""
    jarvis_slugs = [m.jarvis for m in MAPPINGS]
    openclaw_slugs = [m.openclaw for m in MAPPINGS]
    assert len(jarvis_slugs) == len(set(jarvis_slugs))
    assert len(openclaw_slugs) == len(set(openclaw_slugs))


def test_mappings_match_dict_size() -> None:
    """Drift-Guard: alle abgeleiteten Dicts haben dieselbe Groesse wie MAPPINGS."""
    assert len(JARVIS_TO_OPENCLAW) == len(MAPPINGS)
    assert len(OPENCLAW_TO_JARVIS) == len(MAPPINGS)


def test_provider_mapping_is_frozen() -> None:
    """ProviderMapping ist frozen — kein Runtime-Tampering."""
    m = ProviderMapping("test", "test", "TEST_KEY")
    with pytest.raises((AttributeError, TypeError)):
        m.jarvis = "hacked"  # type: ignore[misc]


def test_ad6_table_is_complete() -> None:
    """AD-6 Amendment listet exakt 5 Provider — Drift-Schutz gegen versehentliches Loeschen."""
    expected_jarvis_slugs = {"gemini", "claude-api", "openai", "openrouter", "grok"}
    actual = {m.jarvis for m in MAPPINGS}
    assert actual == expected_jarvis_slugs, (
        "AD-6 Amendment-Tabelle weicht ab — bitte docs/openclaw-bridge.md "
        "und MAPPINGS synchron halten."
    )
