"""Defense-in-depth: a configured subagent provider can NEVER be silently
diverted to the Gemini API key by a per-step model string.

User mandate: heavy tasks run on the configured provider (claude-api ->
Claude Max OAuth subscription). Gemini must never be a silent fallback.

These tests pin the pure routing decision in
``jarvis.missions.init._select_subagent_worker_kind`` so the worker that runs
never drifts from the configured ``[brain.sub_jarvis].provider``.
"""
from __future__ import annotations

import pytest

from jarvis.missions.init import _select_subagent_worker_kind


# --- HARD LOCK: claude-api wins over ANY step model ----------------------


@pytest.mark.parametrize(
    "step_model",
    ["", "gemini-3.1-pro-preview", "gemini", "GEMINI-X", "sonnet", "grok-4.3"],
)
def test_claude_api_is_a_hard_lock(step_model: str) -> None:
    """With claude-api configured, NO step model can route elsewhere —
    especially not to the Gemini worker (which uses the Gemini API key)."""
    assert _select_subagent_worker_kind("claude-api", step_model) == "claude_direct"


def test_openclaw_claude_routes_subjarvis() -> None:
    assert _select_subagent_worker_kind("openclaw-claude", "gemini-x") == "subjarvis"


@pytest.mark.parametrize("provider", ["chatgpt", "openai-codex"])
def test_codex_providers_route_codex(provider: str) -> None:
    assert _select_subagent_worker_kind(provider, "gemini-x") == "codex_direct"


@pytest.mark.parametrize("provider", ["grok", "openai", "openrouter"])
def test_other_providers_route_subjarvis(provider: str) -> None:
    """Even with a gemini step model, a non-empty provider goes via OpenClaw,
    never directly to the Gemini API worker."""
    assert _select_subagent_worker_kind(provider, "gemini-3.1-pro") == "subjarvis"


@pytest.mark.parametrize("step_model", ["", "claude-opus-4-8", "gemini-3.1-pro"])
def test_antigravity_routes_to_oauth_cli_worker(step_model: str) -> None:
    """Choosing 'antigravity' (Google subscription) routes to the dedicated
    OAuth-CLI worker kind — never the API-key Gemini path. Like claude-api, it
    is a hard lock that no step model can divert."""
    assert _select_subagent_worker_kind("antigravity", step_model) == "antigravity"


def test_gemini_as_subagent_provider_uses_direct_gemini_worker() -> None:
    """Post-Welle-4: explicitly choosing 'gemini' routes to the direct
    GeminiWorker so the sub-agent actually RUNS on Gemini. The OpenClaw path it
    used to take was removed, so without this it silently ran on Claude. This is
    an EXPLICIT selection, NOT the anti-silent-Gemini fallback case."""
    assert _select_subagent_worker_kind("gemini", "") == "gemini"
    assert _select_subagent_worker_kind("gemini", "claude-opus-4-8") == "gemini"


# --- Legacy fallback: gemini worker ONLY when NOTHING is configured ------


def test_gemini_worker_only_when_no_provider_configured() -> None:
    assert _select_subagent_worker_kind(None, "gemini-3.1-pro") == "gemini"
    assert _select_subagent_worker_kind("", "gemini-3.1-pro") == "gemini"


def test_unconfigured_non_gemini_defaults_to_subjarvis() -> None:
    assert _select_subagent_worker_kind(None, "sonnet") == "subjarvis"
    assert _select_subagent_worker_kind(None, "") == "subjarvis"
