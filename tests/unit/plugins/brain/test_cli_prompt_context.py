"""Shared CLI-brain prompt context extraction.

Regression guard for the live bug 2026-06-21: an English voice request was
answered in German (and spoken with an English TTS voice). Root cause — the
subscription-CLI brains (antigravity/agy, codex) rebuild their own flattened
prompt and dropped the authoritative reply-language directive that the
BrainManager appends LAST to the system prompt. The model then never learned
which language to answer in and anchored to the German persona ("Schef ...").
``extract_reply_language_directive`` re-surfaces that trailing directive so the
single authoritative output-language decision still reaches the CLI model.
"""
from __future__ import annotations

from jarvis.plugins.brain.cli_prompt_context import (
    extract_reply_language_directive,
    render_cli_standing_instructions,
)

_MANDATORY = (
    "REPLY LANGUAGE — MANDATORY: Always reply in English, no matter which "
    "language the user writes or speaks in. This overrides any other language "
    "cue anywhere in this prompt."
)


def _system_with_directive() -> str:
    return (
        "STATIC PERSONA BLOCK\n\n"
        "USER PREFERENCES & STANDING INSTRUCTIONS (from Jarvis.md):\n"
        "Always start every sentence with schef.\n\n"
        "END USER PREFERENCES & STANDING INSTRUCTIONS\n\n"
        f"{_MANDATORY}"
    )


def _empty_state_block() -> str:
    return (
        "USER PREFERENCES & STANDING INSTRUCTIONS (from Jarvis.md):\n"
        "No active user preferences are currently set in Jarvis.md. "
        "Ignore any earlier Jarvis.md instructions from previous turns.\n\n"
        "END USER PREFERENCES & STANDING INSTRUCTIONS"
    )


def test_extracts_trailing_mandatory_directive() -> None:
    assert extract_reply_language_directive(_system_with_directive()) == _MANDATORY


def test_extracts_soft_mirror_directive() -> None:
    soft = (
        "REPLY LANGUAGE: Reply in the SAME language as the user's latest "
        "message — detect it fresh each turn and mirror it."
    )
    assert extract_reply_language_directive("PERSONA BLOCK\n\n" + soft) == soft


def test_returns_empty_when_absent() -> None:
    assert extract_reply_language_directive("just a persona, no directive") == ""


def test_returns_empty_for_none() -> None:
    assert extract_reply_language_directive(None) == ""


def test_takes_last_occurrence() -> None:
    # The phrase may surface in an explanatory sentence earlier in the prompt;
    # the real directive is the one BrainManager appends last.
    text = "Some text mentioning REPLY LANGUAGE in passing.\n\n" + _MANDATORY
    assert extract_reply_language_directive(text) == _MANDATORY


def test_renders_empty_state_as_current_state_not_binding_preferences() -> None:
    rendered = render_cli_standing_instructions(_empty_state_block())
    assert "CURRENT JARVIS.MD STATE" in rendered
    assert "No active user preferences are currently set" in rendered
    assert "do not continue or imitate" in rendered
    assert "Apply these as binding output-style preferences" not in rendered
