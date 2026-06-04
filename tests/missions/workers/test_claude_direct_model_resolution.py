"""Regression guard for ClaudeDirectWorker model resolution.

Context: ClaudeDirectWorker became the *universal* heavy worker after the
OpenClaw-subprocess SubJarvisWorker was removed in Welle 4 — jarvis.missions.init
routes every provider (grok / gemini / openrouter / unset) to it. The old
implementation hard-failed every mission that did not resolve a "claude-api"
primary provider with:

    "ClaudeDirectWorker: primary provider is grok, expected claude-api"

On any fresh install without an explicit ``[brain.sub_jarvis].provider =
"claude-api"`` the provider chain falls back to ("grok", "grok-4.3"), so the
guard fired for EVERY mission. ``_resolve_claude_model`` replaced the guard:
it always yields a CLAUDE-valid ``--model`` value and never lets a foreign
provider model reach ``claude --model``.
"""
from __future__ import annotations

from dataclasses import dataclass

from jarvis.missions.workers.claude_direct_worker import (
    _DEFAULT_CLAUDE_MODEL,
    _resolve_claude_model,
)


@dataclass(frozen=True)
class _Primary:
    provider: str
    model: str


def test_claude_api_primary_honoured() -> None:
    """A configured claude-api primary keeps its exact model id."""
    assert (
        _resolve_claude_model(_Primary("claude-api", "claude-sonnet-4-6"), "sonnet")
        == "claude-sonnet-4-6"
    )


def test_foreign_provider_falls_back_not_errors() -> None:
    """grok primary with a foreign step model must NOT pass grok-4.3 to claude.

    This is the exact fresh-install failure path: no [brain.sub_jarvis] ->
    provider chain default ("grok", "grok-4.3"). The worker must run on a
    claude model instead of failing the mission.
    """
    assert (
        _resolve_claude_model(_Primary("grok", "grok-4.3"), "grok-4.3")
        == _DEFAULT_CLAUDE_MODEL
    )


def test_foreign_provider_keeps_claude_alias_step_model() -> None:
    """When the Decomposer emits a claude alias, honour it even for grok primary."""
    assert _resolve_claude_model(_Primary("grok", "grok-4.3"), "opus") == "opus"


def test_no_primary_unset_config_defaults_to_claude() -> None:
    """No resolvable primary (empty/missing config) still yields a claude model."""
    assert _resolve_claude_model(None, "") == _DEFAULT_CLAUDE_MODEL


def test_explicit_claude_model_id_passthrough() -> None:
    """A full claude-* id in the step model is passed through verbatim."""
    assert (
        _resolve_claude_model(_Primary("openrouter", "anthropic/claude-x"), "claude-opus-4-8")
        == "claude-opus-4-8"
    )


def test_default_is_a_known_claude_alias() -> None:
    """The fallback must be a model the claude CLI actually accepts."""
    assert _DEFAULT_CLAUDE_MODEL in {"sonnet", "opus", "haiku"}
