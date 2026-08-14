"""Pins for the errand gate — an errand starts ONLY on a real-world order.

The incident this protects against (maintainer, 2026-08-14): "I need the
agentic IDE mode or the last transcription" opened a background errand and
the {name}-Agents board filled with agents nobody asked for. The gate is
deterministic code, because the tool description alone ("not for questions")
was ignored — the same lesson the spawn gate learned in 2026-07.
"""

from __future__ import annotations

import pytest

from jarvis.brain.errand_gate import (
    AGENTIC_IDE_BLOCKED_FEEDBACK,
    ERRAND_VEHICLE_TOOL_NAME,
    agentic_ide_blocks_agents,
    errand_block_reason,
    errand_blocked_feedback,
    llm_errand_allowed,
)

# ----------------------------------------------------------------------
# Blocked shapes — questions, UI requests, research
# ----------------------------------------------------------------------

BLOCKED = [
    # The original incident, verbatim shape: UI/meta object, no order verb.
    # i18n-allow: speech-input vocabulary
    "Ich brauche den Agentic-IDE-Modus oder die letzte Transkription",
    "Zeig mir den Verlauf",  # i18n-allow: speech-input vocabulary
    "Was ist der Agentic-IDE-Modus?",  # i18n-allow: speech-input vocabulary
    "Wie funktioniert das mit den Errands?",  # i18n-allow: speech-input vocabulary
    "Warum hat das gestern nicht geklappt?",  # i18n-allow: speech-input vocabulary
    "Kannst du mir sagen, was sich geändert hat?",  # i18n-allow: speech-input vocabulary
    "What is the fastest route to the airport?",
    "How does the errand system work?",
    "Can you tell me what changed yesterday?",
    "open the settings",
    "show me the last transcription",
    "Finde heraus, ob der Flug billiger wird",  # i18n-allow: speech-input vocabulary
    "look up the opening hours",
    "¿Qué modo está activo?",  # i18n-allow: speech-input vocabulary
    "",
]

# ----------------------------------------------------------------------
# Allowed shapes — real-world orders, polite question form included
# ----------------------------------------------------------------------

ALLOWED = [
    "Buch mir einen Flug nach Nizza",  # i18n-allow: speech-input vocabulary
    "Kannst du mir bitte eine Pizza bestellen?",  # i18n-allow: speech-input vocabulary
    "Melde mich beim Newsletter von heise an",  # i18n-allow: speech-input vocabulary
    "Kündige mein Fitnessstudio",  # i18n-allow: speech-input vocabulary
    "order the printer cartridges",
    "reserve a table for two at eight",
    "sign me up for the newsletter",
    "cancel my gym membership",
    # "open" with a REAL-world object is an errand, not a UI request.
    "open a savings account at the bank and get me the paperwork",
    # A UI word plus a genuine order verb stays an order.
    "Buch den Flug und leg die Buchung in den Verlauf",  # i18n-allow: speech-input vocabulary
]


@pytest.mark.parametrize("utterance", BLOCKED)
def test_questions_and_ui_requests_never_open_an_errand(utterance: str) -> None:
    assert llm_errand_allowed(utterance) is False, utterance
    assert errand_block_reason(utterance) is not None


@pytest.mark.parametrize("utterance", ALLOWED)
def test_real_world_orders_pass_polite_question_form_included(utterance: str) -> None:
    assert llm_errand_allowed(utterance) is True, (
        utterance,
        errand_block_reason(utterance),
    )


def test_feedback_names_the_reason_and_the_right_move() -> None:
    text = errand_blocked_feedback("show me the last transcription")
    assert "was not executed" in text
    assert "ui-meta-request" in text
    assert "answer it directly" in text


def test_vehicle_name_matches_the_registered_tool() -> None:
    from jarvis.plugins.tool.start_errand import StartErrandTool

    assert ERRAND_VEHICLE_TOOL_NAME == StartErrandTool.name


# ----------------------------------------------------------------------
# Screen gate — no background agents while the Agentic IDE is visible
# ----------------------------------------------------------------------


def test_agentic_ide_on_screen_blocks_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jarvis.agentic_ide.session.agentic_ide_on_screen", lambda: True
    )
    assert agentic_ide_blocks_agents() is True
    assert "Agentic IDE" in AGENTIC_IDE_BLOCKED_FEEDBACK


def test_other_sections_do_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jarvis.agentic_ide.session.agentic_ide_on_screen", lambda: False
    )
    assert agentic_ide_blocks_agents() is False


def test_a_broken_surface_never_silences_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown state degrades to 'do not block' — an optional UI surface must
    not be able to mute ordinary agent starts."""

    def _boom() -> bool:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr("jarvis.agentic_ide.session.agentic_ide_on_screen", _boom)
    assert agentic_ide_blocks_agents() is False
