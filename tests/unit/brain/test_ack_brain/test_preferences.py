"""User standing-preferences must reach the flash spoken tiers.

The deep brain already injects the user's agent-instructions file. These lock
that the two OTHER LLM-generated spoken surfaces — the pre-thinking ack preamble
(AckGenerator) and the spawn announcement (SpawnAnnouncementComposer) — also
receive the preferences via their ``preferences_provider`` hook, so a preference
like "start every sentence with 'Chef'" is honored on action turns too (where
the spoken output comes from these tiers, not the deep brain).

Deterministic surfaces (the curated spawn fallback pool, CU readbacks, canned
status phrases) are fixed strings and intentionally NOT covered here.
"""
from __future__ import annotations

import types

from jarvis.brain.ack_brain import AckGenerator, CircuitBreaker
from jarvis.brain.ack_brain.persona_prompt import get_persona_prompt
from jarvis.brain.ack_brain.spawn_announcement import (
    SpawnAnnouncementComposer,
    get_spawn_persona,
)


class _RecordingProvider:
    """Minimal AbstractAckProvider that records the persona_prompt it receives."""

    def __init__(self, reply: str = "Chef, ich schaue gleich in dein Gmail.") -> None:
        self.reply = reply
        self.persona_prompts: list[str] = []

    async def run(self, utterance: str, language: str, *, persona_prompt: str) -> str:
        self.persona_prompts.append(persona_prompt)
        return self.reply


def _ack_config() -> types.SimpleNamespace:
    # AckGenerator.run only touches .timeout_ms; __init__ only reads .provider.
    return types.SimpleNamespace(timeout_ms=1500, provider="fake")


# --------------------------------------------------------------------------- #
# Ack preamble (AckGenerator)                                                  #
# --------------------------------------------------------------------------- #


async def test_ack_prompt_includes_user_preferences_when_set() -> None:
    provider = _RecordingProvider()
    gen = AckGenerator(
        provider=provider,
        config=_ack_config(),
        breaker=CircuitBreaker(threshold=3, cooldown_s=60),
        preferences_provider=lambda: "PREFS-MARKER-XYZZY",
    )
    await gen.run("öffne mein Gmail und räum auf", language="de")
    assert provider.persona_prompts, "the provider should have been called"
    prompt = provider.persona_prompts[0]
    assert "PREFS-MARKER-XYZZY" in prompt
    assert "Vor-Antwort" in prompt  # base persona still present (name-neutral marker)


async def test_ack_prompt_is_base_persona_when_no_preferences() -> None:
    provider = _RecordingProvider()
    gen = AckGenerator(
        provider=provider,
        config=_ack_config(),
        breaker=CircuitBreaker(threshold=3, cooldown_s=60),
        preferences_provider=lambda: "",
    )
    await gen.run("such Flüge nach Berlin", language="de")
    assert provider.persona_prompts[0] == get_persona_prompt("de")


async def test_ack_prompt_unaffected_when_provider_raises() -> None:
    # A faulty preferences hook must never break the ack (silent-on-failure).
    def _boom() -> str:
        raise RuntimeError("disk gone")

    provider = _RecordingProvider()
    gen = AckGenerator(
        provider=provider,
        config=_ack_config(),
        breaker=CircuitBreaker(threshold=3, cooldown_s=60),
        preferences_provider=_boom,
    )
    await gen.run("such Flüge nach Berlin", language="de")
    assert provider.persona_prompts[0] == get_persona_prompt("de")


# --------------------------------------------------------------------------- #
# Spawn announcement (SpawnAnnouncementComposer)                               #
# --------------------------------------------------------------------------- #


async def test_spawn_llm_prompt_includes_user_preferences_when_set() -> None:
    provider = _RecordingProvider(reply="Chef, ich schaue gründlich in dein Gmail.")
    composer = SpawnAnnouncementComposer(
        provider=provider,
        config=_ack_config(),
        breaker=None,
        preferences_provider=lambda: "SPAWN-PREFS-MARKER",
    )
    await composer.compose(utterance="öffne mein Gmail und räum auf", language="de")
    assert provider.persona_prompts, "the LLM path should have been taken"
    prompt = provider.persona_prompts[0]
    assert "SPAWN-PREFS-MARKER" in prompt
    assert prompt.startswith(get_spawn_persona("de")[:40])  # base spawn persona present


async def test_spawn_llm_prompt_is_base_persona_when_no_preferences() -> None:
    provider = _RecordingProvider(reply="Ich schaue gründlich in dein Gmail.")
    composer = SpawnAnnouncementComposer(
        provider=provider,
        config=_ack_config(),
        breaker=None,
        preferences_provider=lambda: "",
    )
    await composer.compose(utterance="öffne mein Gmail und räum auf", language="de")
    assert provider.persona_prompts[0] == get_spawn_persona("de")
