"""Tests for resolve_assistant_name — the configurable assistant identity."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.brain.assistant_name import DEFAULT_ASSISTANT_NAME, resolve_assistant_name


def _cfg(*, persona_name: str = "", wake_phrase: str | None = None) -> SimpleNamespace:
    trigger = SimpleNamespace(
        wake_word=SimpleNamespace(phrase=wake_phrase) if wake_phrase is not None else None
    )
    return SimpleNamespace(
        persona=SimpleNamespace(name=persona_name),
        trigger=trigger,
    )


# ----------------------------------------------------------------------
# Derivation from the wake phrase (the primary path — one field for the user)
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("Hey Jarvis", "Jarvis"),     # prefix stripped
        ("Jarvis", "Jarvis"),
        ("Micron", "Micron"),         # arbitrary name
        ("micron", "Micron"),         # lowercase → title-cased
        ("Hey Athena", "Athena"),
        ("Alexa", "Alexa"),
        ("Hey Computer", "Computer"),
        ("ok friday", "Friday"),
    ],
)
def test_name_derived_from_wake_phrase(phrase, expected):
    assert resolve_assistant_name(_cfg(wake_phrase=phrase)) == expected


# ----------------------------------------------------------------------
# Explicit override wins
# ----------------------------------------------------------------------

def test_explicit_persona_name_overrides_wake_phrase():
    # Wake "Hey Computer" but identity pinned to "Friday".
    cfg = _cfg(persona_name="Friday", wake_phrase="Hey Computer")
    assert resolve_assistant_name(cfg) == "Friday"


def test_explicit_name_is_trimmed():
    assert resolve_assistant_name(_cfg(persona_name="  Vision  ")) == "Vision"


# ----------------------------------------------------------------------
# Fallback safety — must never crash, always returns a usable name
# ----------------------------------------------------------------------

def test_falls_back_to_default_when_no_phrase_and_no_override():
    assert resolve_assistant_name(_cfg(wake_phrase="")) == DEFAULT_ASSISTANT_NAME


def test_falls_back_when_wake_word_missing():
    assert resolve_assistant_name(_cfg(wake_phrase=None)) == DEFAULT_ASSISTANT_NAME


def test_falls_back_on_completely_empty_config():
    assert resolve_assistant_name(SimpleNamespace()) == DEFAULT_ASSISTANT_NAME


def test_falls_back_on_none_config():
    assert resolve_assistant_name(None) == DEFAULT_ASSISTANT_NAME


def test_whitespace_only_phrase_falls_back():
    assert resolve_assistant_name(_cfg(wake_phrase="   ")) == DEFAULT_ASSISTANT_NAME
