"""Tests for jarvis.missions.worker_runtime.provider_map.

Source of truth: docs/openclaw-bridge.md AD-6 Amendment table.
When the table changes, update BOTH — the doc is authoritative.
"""
from __future__ import annotations

import pytest

from jarvis.missions.worker_runtime.provider_map import (
    JARVIS_TO_WORKER_SLUG,
    MAPPINGS,
    WORKER_SLUG_TO_JARVIS,
    ProviderMapping,
    NoWorkerSlugMappingError,
    NoJarvisFromWorkerSlugError,
    env_vars_for,
    to_jarvis_from_worker_slug,
    to_worker_slug,
    validate_configured_providers,
)


# --- Forward-Mapping (jarvis -> worker slug) ---


@pytest.mark.parametrize(
    "jarvis_slug,worker_slug",
    [
        ("gemini", "google"),
        # 2026-05-17 — claude-api now routes through the worker's claude-cli
        # backend (OAuth, Claude Max subscription) instead of `anthropic`
        # (paid Messages API with extra-usage requirement). See
        # provider_map.MAPPINGS docstring + docs/openclaw-bridge.md AD-6.
        ("claude-api", "claude-cli"),
        ("openai", "openai"),
        ("openrouter", "openrouter"),
        ("grok", "xai"),
    ],
)
def test_to_worker_slug_known_providers(jarvis_slug: str, worker_slug: str) -> None:
    """All documented providers map as in the AD-6 Amendment table."""
    assert to_worker_slug(jarvis_slug) == worker_slug


def test_to_worker_slug_groq_brain_raises() -> None:
    """Groq is STT-only and has no Brain or Jarvis-Agent mapping."""
    with pytest.raises(NoWorkerSlugMappingError):
        to_worker_slug("groq")


def test_to_worker_slug_unknown_raises() -> None:
    with pytest.raises(NoWorkerSlugMappingError) as exc_info:
        to_worker_slug("ollama-local")
    msg = str(exc_info.value)
    assert "ollama-local" in msg
    assert "claude-api" in msg  # hint about known mappings
    assert "MAPPINGS" in msg  # hint where to extend


def test_to_worker_slug_empty_string_raises() -> None:
    with pytest.raises(NoWorkerSlugMappingError):
        to_worker_slug("")


def test_to_worker_slug_case_sensitive() -> None:
    """Slugs are lowercase — case mismatch is not auto-matched."""
    with pytest.raises(NoWorkerSlugMappingError):
        to_worker_slug("Gemini")


# --- Reverse-Mapping (worker slug -> jarvis) ---


@pytest.mark.parametrize(
    "worker_slug,jarvis_slug",
    [
        ("google", "gemini"),
        ("claude-cli", "claude-api"),
        ("openai", "openai"),
        ("openrouter", "openrouter"),
        ("xai", "grok"),
    ],
)
def test_to_jarvis_from_worker_slug_round_trip(worker_slug: str, jarvis_slug: str) -> None:
    assert to_jarvis_from_worker_slug(worker_slug) == jarvis_slug


def test_to_jarvis_from_worker_slug_unknown_raises() -> None:
    with pytest.raises(NoJarvisFromWorkerSlugError) as exc_info:
        to_jarvis_from_worker_slug("unknown-worker")
    assert "unknown-worker" in str(exc_info.value)


def test_round_trip_jarvis_worker_jarvis() -> None:
    for jarvis_slug in JARVIS_TO_WORKER_SLUG:
        assert to_jarvis_from_worker_slug(to_worker_slug(jarvis_slug)) == jarvis_slug


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
    """ENV-var set matches AD-6 Amendment, order: primary, fallback."""
    assert env_vars_for(jarvis_slug) == expected_env


def test_env_vars_for_unknown_raises() -> None:
    with pytest.raises(NoWorkerSlugMappingError):
        env_vars_for("ollama-local")


def test_env_vars_primary_always_first() -> None:
    """Bridge should set primary-ENV first — both set is more robust."""
    primary, *_ = env_vars_for("gemini")
    assert primary == "GEMINI_API_KEY"


# --- Validation-Helper ---


def test_validate_configured_providers_all_mapped_returns_empty() -> None:
    configured = ["gemini", "claude-api", "openai", "openrouter", "grok"]
    assert validate_configured_providers(configured) == []


def test_validate_configured_providers_unmapped_listed() -> None:
    configured = ["gemini", "ollama-local", "openai", "codex"]
    unmapped = validate_configured_providers(configured)
    assert unmapped == ["codex", "ollama-local"]  # alphabetically sorted


def test_validate_configured_providers_empty_input() -> None:
    assert validate_configured_providers([]) == []


def test_validate_configured_providers_consumes_iterator() -> None:
    """Iterable argument may also be a generator."""

    def gen() -> object:
        yield "gemini"
        yield "future-provider"

    assert validate_configured_providers(gen()) == ["future-provider"]


def test_validate_configured_providers_order_stable() -> None:
    """Result is alphabetically sorted, independent of input order."""
    a = validate_configured_providers(["zzz", "aaa", "mmm"])
    b = validate_configured_providers(["aaa", "mmm", "zzz"])
    assert a == b == ["aaa", "mmm", "zzz"]


# --- Table/data consistency ---


def test_mappings_are_unique_in_both_directions() -> None:
    """No duplicate jarvis slug, no duplicate worker slug."""
    jarvis_slugs = [m.jarvis for m in MAPPINGS]
    worker_slugs = [m.worker_slug for m in MAPPINGS]
    assert len(jarvis_slugs) == len(set(jarvis_slugs))
    assert len(worker_slugs) == len(set(worker_slugs))


def test_mappings_match_dict_size() -> None:
    """Drift guard: all derived dicts have the same size as MAPPINGS."""
    assert len(JARVIS_TO_WORKER_SLUG) == len(MAPPINGS)
    assert len(WORKER_SLUG_TO_JARVIS) == len(MAPPINGS)


def test_provider_mapping_is_frozen() -> None:
    """ProviderMapping is frozen — no runtime tampering."""
    m = ProviderMapping("test", "test", "TEST_KEY")
    with pytest.raises((AttributeError, TypeError)):
        m.jarvis = "hacked"  # type: ignore[misc]


def test_ad6_table_is_complete() -> None:
    """AD-6 table drift guard — the set of subagent-selectable brain providers.

    Grok uses the documented ``grok->xai`` row. ``nvidia`` (NVIDIA NIM) is an
    OpenAI-compatible API brain that, like ``openai``/``openrouter``, runs on the
    in-process ApiAgentWorker (not the OpenClaw CLI harness); its row exists so it
    is a selectable Jarvis-Agent in the API-Keys view."""
    expected_jarvis_slugs = {
        "gemini",
        "claude-api",
        "openai",
        "openrouter",
        "grok",
        "nvidia",
    }
    actual = {m.jarvis for m in MAPPINGS}
    assert actual == expected_jarvis_slugs, (
        "AD-6 Amendment table drifted — please keep docs/openclaw-bridge.md "
        "and MAPPINGS in sync."
    )


# --- Antigravity (Google subscription via OAuth CLI) subagent slug ---


def test_antigravity_subagent_slugs_ssot() -> None:
    """The Antigravity subagent slug set is the SSoT mirror of CODEX_SUBAGENT_SLUGS:
    it routes to the dedicated OAuth-CLI worker, NOT through MAPPINGS (no worker
    slug in the provider map)."""
    from jarvis.missions.worker_runtime.provider_map import (
        ANTIGRAVITY_SUBAGENT_CANONICAL,
        ANTIGRAVITY_SUBAGENT_SLUGS,
    )

    assert "antigravity" in ANTIGRAVITY_SUBAGENT_SLUGS
    assert ANTIGRAVITY_SUBAGENT_CANONICAL == "antigravity"
    # Not a MAPPINGS provider (OAuth CLI has no worker slug, like codex).
    assert "antigravity" not in JARVIS_TO_WORKER_SLUG


def test_codex_subagent_slugs_accept_bare_codex_alias() -> None:
    """The voice gate emits the bare spoken word "codex"; the acceptance set must
    accept it. Forensic 2026-06-27: "set the subagent to codex" answered "codex
    is not a valid provider" because only "openai-codex"/"chatgpt" were accepted.
    "openai-codex" stays the canonical persisted value."""
    from jarvis.missions.worker_runtime.provider_map import (
        CODEX_SUBAGENT_CANONICAL,
        CODEX_SUBAGENT_SLUGS,
    )

    assert "codex" in CODEX_SUBAGENT_SLUGS
    assert "openai-codex" in CODEX_SUBAGENT_SLUGS
    assert "chatgpt" in CODEX_SUBAGENT_SLUGS
    assert CODEX_SUBAGENT_CANONICAL == "openai-codex"
