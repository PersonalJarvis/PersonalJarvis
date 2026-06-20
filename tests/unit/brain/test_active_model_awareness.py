"""The active brain provider/model must reach the system prompt.

User mandate 2026-06-20 (voice session 15:15): Jarvis kept hallucinating that
it was "Gemini" when asked which provider was active — even though Grok was the
live provider that actually answered the turn (sessions.db: provider=grok,
tool_calls=[]). Root cause: ``_build_system_prompt`` never told the answering
LLM which provider/model it was embodying, so a provider question got a guessed
answer that defaulted to "Gemini".

These lock that the per-turn provider identity (set in the generate() fallback
loop, where the real ``prov_name``/``model`` are known) flows into the system
prompt as an authoritative, anti-guessing infrastructure fact.
"""
from __future__ import annotations

from jarvis.brain.manager import (
    BrainManager,
    _provider_display_name,
    _provider_identity_directive,
)
from jarvis.core.config import load_config


def _manager(*, wake_phrase: str = "Hey Jarvis") -> BrainManager:
    """A BrainManager with __init__ bypassed — only the attrs the prompt needs."""
    m = BrainManager.__new__(BrainManager)
    m._soul = None
    m._user_profile = None
    m._people = None
    m._core_memory = None
    m._awareness_manager = None
    m._system_prompt_extra = "ROUTER DISCIPLINE BLOCK"
    m._wiki_context_suffix = ""
    m._reply_language = "auto"
    m._active_turn_identity = None
    cfg = load_config()
    cfg.performance.cache_optimized_prompt = False
    cfg.trigger.wake_word.phrase = wake_phrase
    m._config = cfg
    return m


# --------------------------------------------------------------------------
# Pure helper: provider display names
# --------------------------------------------------------------------------


def test_display_name_known_providers() -> None:
    assert "Grok" in _provider_display_name("grok")
    assert "Gemini" in _provider_display_name("gemini")
    assert "Claude" in _provider_display_name("claude-api")
    # The user explicitly wants Codex/GPT-5.5 to be recognised.
    codex = _provider_display_name("openai-codex")
    assert "Codex" in codex or "GPT" in codex


def test_display_name_unknown_provider_falls_back_readably() -> None:
    # Never crash on an unmapped id — produce a readable label.
    assert _provider_display_name("some-new-provider")
    assert "_" not in _provider_display_name("some-new-provider")


# --------------------------------------------------------------------------
# Pure helper: the directive text
# --------------------------------------------------------------------------


def test_identity_directive_names_provider_and_model() -> None:
    d = _provider_identity_directive("grok", "grok-4.3", "Jarvis")
    assert "Grok" in d
    assert "grok-4.3" in d
    # Must instruct against guessing / defaulting to Gemini.
    assert "Gemini" in d  # named as the forbidden default
    assert "never" in d.lower() or "not" in d.lower()


# --------------------------------------------------------------------------
# Integration: it reaches _build_system_prompt
# --------------------------------------------------------------------------


def test_active_grok_identity_in_system_prompt() -> None:
    m = _manager()
    m._active_turn_identity = ("grok", "grok-4.3")
    prompt = m._build_system_prompt()
    assert "Grok" in prompt
    assert "grok-4.3" in prompt
    # The anti-hallucination instruction is present (generic anti-guess rule,
    # with the known "Gemini" failure named as the forbidden exemplar).
    assert "never guess" in prompt.lower()
    assert "Gemini" in prompt


def test_active_codex_identity_says_gpt() -> None:
    m = _manager()
    m._active_turn_identity = ("openai-codex", "gpt-5.5")
    prompt = m._build_system_prompt()
    assert ("Codex" in prompt) or ("GPT" in prompt)
    assert "gpt-5.5" in prompt


def test_no_identity_block_when_unset() -> None:
    m = _manager()
    m._active_turn_identity = None
    prompt = m._build_system_prompt()
    # No authoritative model block, and crucially no spurious provider claim.
    assert "ACTIVE BRAIN MODEL" not in prompt


def test_grok_active_does_not_claim_to_be_gemini() -> None:
    # The exact regression: Grok is live, the prompt must not assert the model
    # IS Gemini. "Gemini" may appear only as the explicitly-forbidden default.
    m = _manager()
    m._active_turn_identity = ("grok", "grok-4.3")
    prompt = m._build_system_prompt()
    assert "running on the brain provider Grok" in prompt
    # It must never say it is *running on* Gemini.
    assert "running on the brain provider Google Gemini" not in prompt
