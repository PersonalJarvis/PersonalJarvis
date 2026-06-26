"""VoiceCommandGate — the LIVE deterministic provider-switch / cancel / depth
detector (wired via BrainManager._detect_switch_intent -> match_voice_command).

Regression guard added 2026-06-08: a voice "switch the brain provider to X" was
NOT caught by the strict pattern (the "brain provider" filler between the verb
and "to" broke it), fell through to the router LLM, and the LLM — told in its
system prompt it had "no authority" to switch — refused with "keine Berechtigung".
The gate must tolerate the natural "den/the [brain] provider/anbieter" filler.
"""
from __future__ import annotations

import pytest

from jarvis.brain.voice_command_gate import match_voice_command


@pytest.mark.parametrize(
    "text,target",
    [
        # existing, must keep working
        ("wechsel auf gemini", "gemini"),
        ("switch to openai", "openai"),
        ("nutze openai", "openai"),
        ("wechsle zu claude", "claude"),
        ("use openrouter", "openrouter"),
        # NEW: natural phrasings with a provider-noun filler
        ("switch the brain provider to gemini", "gemini"),
        ("wechsel den Brain-Provider auf gemini", "gemini"),
        ("wechsel den Provider auf openrouter", "openrouter"),
        ("wechsle den Anbieter zu openrouter", "openrouter"),
        ("switch provider to claude", "claude"),
        ("switch the provider to openai", "openai"),
        ("wechsel deinen Provider auf gemini", "gemini"),
    ],
)
def test_provider_switch_matches(text: str, target: str) -> None:
    m = match_voice_command(text)
    assert m is not None and m.kind == "provider_switch", f"no match for {text!r}"
    assert m.target == target


@pytest.mark.parametrize(
    "text",
    [
        "ich gehe auf meinem Weg",
        "wie spät ist es",
        "erzähl mir was über gemini",  # a mention, not a switch command
        # Grok was removed as a brain provider (only grok-voice TTS + the
        # grok_api_key credential remain), so it is no longer a recognized
        # brain/provider switch target — "switch to grok" must NOT match.
        "switch to grok",
        "wechsle den Anbieter zu grok",
    ],
)
def test_harmless_does_not_match_provider(text: str) -> None:
    m = match_voice_command(text)
    assert m is None or m.kind != "provider_switch"


@pytest.mark.parametrize(
    "text",
    [
        "Kannst du eine HTML-Datei machen, was morgen in Englisch drankommen kann?",  # i18n-allow: German voice-command fixture
        "Mach mir eine Uebersicht auf Englisch.",  # i18n-allow: German voice-command fixture
    ],
)
def test_artifact_requests_do_not_switch_reply_language(text: str) -> None:
    m = match_voice_command(text)
    assert m is None or m.kind != "language_switch"


@pytest.mark.parametrize(
    "text,target",
    [
        ("stell auf Englisch um", "en"),  # i18n-allow: German voice-command fixture
        ("antworte ab jetzt auf Englisch", "en"),  # i18n-allow: German voice-command fixture
        ("respond in German", "de"),
    ],
)
def test_explicit_reply_language_switch_still_matches(text: str, target: str) -> None:
    m = match_voice_command(text)
    assert m is not None and m.kind == "language_switch"
    assert m.target == target


def test_cancel_and_depth_still_work() -> None:
    assert match_voice_command("jarvis stopp").kind == "cancel"
    assert match_voice_command("denk gründlich").kind == "depth_deep"
    assert match_voice_command("nimm haiku").kind == "depth_fast"
