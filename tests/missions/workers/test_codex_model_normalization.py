"""Codex-Worker model-slug normalization (Welle 6 BUG-LIVE follow-up).

Live repro 2026-05-18 mission_019e3c52-0acd:
    Codex with ChatGPT account returned HTTP 400 because the
    MissionDecomposer hardcoded Step.model="sonnet" as default and the
    worker passed it through to `codex exec --model sonnet`. The error
    message verbatim:

        "The 'sonnet' model is not supported when using Codex with a
         ChatGPT account."

The fix is a normalization helper that returns an empty string for
Anthropic-flavoured aliases (sonnet/opus/haiku) and explicit claude-*
/ anthropic-* prefixes. The caller must omit `--model` from argv when
the helper returns empty -- both the Worker and the Critic path do
that via ``if model:`` gating.
"""
from __future__ import annotations

import pytest

from jarvis.missions.workers.codex_direct_worker import (
    _CLAUDE_MODEL_ALIASES,
    _normalize_model_for_codex,
)


# --- Anthropic-flavoured aliases (legacy decomposer defaults) ---


@pytest.mark.parametrize("alias", ["sonnet", "opus", "haiku"])
def test_anthropic_aliases_normalize_to_empty(alias: str) -> None:
    """The MissionDecomposer's three legacy default slugs all map to
    empty so codex uses its ChatGPT-subscription default model."""
    assert _normalize_model_for_codex(alias) == ""


@pytest.mark.parametrize("alias", ["SONNET", "Sonnet", "  sonnet  ", "Opus", "HAIKU"])
def test_anthropic_aliases_match_case_insensitively_and_trimmed(
    alias: str,
) -> None:
    """Defensive lookup -- legacy code may emit any casing or stray spaces."""
    assert _normalize_model_for_codex(alias) == ""


# --- Explicit Anthropic prefixes ---


@pytest.mark.parametrize("model", [
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-haiku-4-5-20251001",
    "Claude-Sonnet-4",
    "anthropic/claude-sonnet",
    "ANTHROPIC-foo",
])
def test_claude_or_anthropic_prefixes_normalize_to_empty(model: str) -> None:
    """Explicit Anthropic slugs (whatever the casing) must also fall
    through to the ChatGPT default. This covers the case where
    `choose_critic_model` returns the full ``claude-sonnet-4-6`` string
    rather than the bare alias."""
    assert _normalize_model_for_codex(model) == ""


# --- Empty / None inputs ---


@pytest.mark.parametrize("empty", [None, "", "   ", "\t\n"])
def test_empty_inputs_normalize_to_empty(empty: str | None) -> None:
    """None and whitespace-only inputs are treated as 'no model'."""
    assert _normalize_model_for_codex(empty) == ""


# --- Pass-through for ChatGPT / OpenAI / other models ---


@pytest.mark.parametrize("model", [
    "gpt-5-codex",
    "gpt-5",
    "gpt-4o",
    "o3",
    "o3-mini",
    "openai/gpt-4",
    # Unknown providers should pass through so future provider plugins
    # are not silently muted.
    "gemini-3.1-pro-preview",
    "grok-4.3",
])
def test_non_anthropic_models_pass_through_unchanged(model: str) -> None:
    """Anything that is not a Claude alias or claude-/anthropic-prefixed
    must arrive at codex verbatim. Otherwise we'd silently mute any
    explicit OpenAI model choice the user configured."""
    assert _normalize_model_for_codex(model) == model


# --- Drift-guard ---


def test_claude_alias_set_is_exactly_three() -> None:
    """The decomposer hardcoded set is sonnet / opus / haiku. Any
    expansion of this set must come with a matching MissionDecomposer
    change so the LLM emits the same vocabulary as the validator
    accepts. Guarded here so a silent enlargement is impossible."""
    assert _CLAUDE_MODEL_ALIASES == frozenset({"sonnet", "opus", "haiku"})


# --- D9 recursion guard: codex must not spawn NESTED sub-agents ---

from pathlib import Path  # noqa: E402

from jarvis.missions.workers.codex_direct_worker import (  # noqa: E402
    _build_codex_direct_cmd,
)


def test_codex_cmd_disables_multi_agent_collab_tools() -> None:
    """A mission worker IS the sub-agent — it must NEVER use codex's native
    multi_agent collaboration tools (spawn_agent / wait) to spawn a NESTED codex
    agent and block on it.

    Live mission 019ec708 (2026-06-14): the prompt was phrased "spawn a
    sub-agent which will help me plan a trip from London to Taiwan"; the codex
    worker called spawn_agent("Hooke") then `wait` and hung for the full worker
    timeout (frozen stream, no WorkerDraftReady for 7+ min). Jarvis's D9
    recursion guard (AP-5 / AP-14: no spawn tool in any worker set) governs
    Jarvis's own tool registry — codex's native feature bypasses it, so the
    argv must disable it. `--disable <FEATURE>` == `-c features.<name>=false`.
    """
    cmd = _build_codex_direct_cmd(worktree=Path("/tmp/wt"), model=None)
    disabled = [
        cmd[i + 1]
        for i, a in enumerate(cmd)
        if a == "--disable" and i + 1 < len(cmd)
    ]
    assert "multi_agent" in disabled, f"multi_agent not disabled in argv: {cmd}"
    assert "multi_agent_v2" in disabled, f"multi_agent_v2 not disabled: {cmd}"
