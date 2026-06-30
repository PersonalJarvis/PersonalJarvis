"""Language resolution + name-neutrality for the Flash-Brain preamble persona.

Added 2026-06-29 with the name-neutral + Spanish rework. Guards that the
preamble resolves de/en/es natively, never bakes in a product name, and carries
no em dashes (which render as hard TTS pauses).
"""
from __future__ import annotations

from jarvis.brain.ack_brain.persona_prompt import (
    PERSONA_PROMPT_DE,
    PERSONA_PROMPT_EN,
    PERSONA_PROMPT_ES,
    get_persona_prompt,
)


def test_get_persona_prompt_resolves_each_language() -> None:
    assert get_persona_prompt("de") == PERSONA_PROMPT_DE
    assert get_persona_prompt("en") == PERSONA_PROMPT_EN
    assert get_persona_prompt("es") == PERSONA_PROMPT_ES


def test_get_persona_prompt_normalises_tags_and_falls_back_to_de() -> None:
    assert get_persona_prompt("en-US") == PERSONA_PROMPT_EN
    assert get_persona_prompt("es-ES") == PERSONA_PROMPT_ES
    assert get_persona_prompt("de-DE") == PERSONA_PROMPT_DE
    # Unknown / empty / None falls back to German (the STT default on ambiguity).
    assert get_persona_prompt("fr") == PERSONA_PROMPT_DE
    assert get_persona_prompt("") == PERSONA_PROMPT_DE
    assert get_persona_prompt(None) == PERSONA_PROMPT_DE


def test_preamble_personas_are_name_neutral() -> None:
    # No baked-in product name: the assistant's name is runtime-derived from the
    # wake word and owned by the deep brain.
    for prompt in (PERSONA_PROMPT_DE, PERSONA_PROMPT_EN, PERSONA_PROMPT_ES):
        assert "JARVIS" not in prompt
        assert "Jarvis" not in prompt


def test_preamble_personas_have_no_em_dash() -> None:
    for prompt in (PERSONA_PROMPT_DE, PERSONA_PROMPT_EN, PERSONA_PROMPT_ES):
        assert "—" not in prompt  # em dash
        assert "–" not in prompt  # en dash
