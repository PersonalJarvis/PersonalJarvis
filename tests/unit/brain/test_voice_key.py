"""The realtime voice key is reserved for the voice call (mandate 2026-09-29)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.brain import voice_key
from jarvis.brain.voice_key import bills_voice_key, voice_key_slots, without_voice_key


def _cfg(mode: str, realtime_provider: str) -> SimpleNamespace:
    return SimpleNamespace(
        voice=SimpleNamespace(mode=mode),
        brain=SimpleNamespace(realtime=SimpleNamespace(provider=realtime_provider)),
    )


def test_openai_live_reserves_the_openai_key() -> None:
    cfg = _cfg("realtime", "openai-live")

    assert "openai_api_key" in voice_key_slots(cfg)
    assert bills_voice_key(cfg, "openai")
    assert not bills_voice_key(cfg, "grok")
    assert not bills_voice_key(cfg, "claude-cli")


def test_pipeline_voice_reserves_nothing() -> None:
    cfg = _cfg("pipeline", "openai-live")

    assert voice_key_slots(cfg) == frozenset()
    assert not bills_voice_key(cfg, "openai")


def test_unknown_or_missing_realtime_provider_reserves_nothing() -> None:
    assert voice_key_slots(_cfg("realtime", "")) == frozenset()
    assert voice_key_slots(_cfg("realtime", "no-such-voice-plugin")) == frozenset()
    assert not bills_voice_key(None, "openai")


def test_chain_drops_the_voice_key_while_another_provider_remains() -> None:
    cfg = _cfg("realtime", "openai-live")
    chain = [("openai", "gpt-5.5-pro"), ("grok", "grok-4.3"), ("openai", "gpt-5.5")]

    assert without_voice_key(cfg, chain) == [("grok", "grok-4.3")]


def test_chain_keeps_the_voice_key_when_nothing_else_can_answer() -> None:
    cfg = _cfg("realtime", "openai-live")
    chain = [("openai", "gpt-5.5"), ("ollama", None)]

    kept = without_voice_key(cfg, chain, is_alternative=lambda provider: provider != "ollama")

    assert kept == chain


def test_chain_keeps_a_named_entry_and_subscription_brains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex is signed in on its plan here. The real probe reads this machine's
    # login, which a CI runner does not have, so the test states it instead.
    monkeypatch.setattr(voice_key, "_signed_in_on_subscription", lambda p: p == "codex")
    cfg = _cfg("realtime", "openai-live")
    lead = ("openai", "gpt-5.5")
    chain = [lead, ("codex", None), ("grok", "grok-4.3")]

    assert without_voice_key(cfg, chain, keep=[lead]) == chain
    assert without_voice_key(cfg, chain) == [("codex", None), ("grok", "grok-4.3")]


def test_pipeline_voice_leaves_the_chain_untouched() -> None:
    chain = [("openai", "gpt-5.5"), ("grok", "grok-4.3")]

    assert without_voice_key(_cfg("pipeline", "openai-live"), chain) == chain
