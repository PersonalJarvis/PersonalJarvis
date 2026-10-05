"""Recurring work is recognised without the word "routine", and a request that
names a skill keeps its skill meaning (live 2026-09-29: "every day at 8"
ended as an inactive draft skill)."""

from __future__ import annotations

import pytest

from jarvis.society.routine_intent import (
    requests_event_triggered_work,
    requests_recurring_work,
    requests_routine_creation,
    wants_agent_routine,
)

ROUTINE_REQUESTS = [
    "Erstelle einen Agenten, der mir jeden Tag um 8 Uhr ein Morgenbriefing gibt",  # i18n-allow
    "gib mir jeden Morgen um acht ein Briefing",  # i18n-allow
    "Kannst du mir täglich meine wichtigsten Gmails zusammenfassen",  # i18n-allow
    "every day at 8 give me a morning briefing",
    "Remind me every Monday to pay rent",
    "dame un resumen cada mañana a las ocho",  # i18n-allow
    "Erstelle eine Routine für den Gmail Agenten",  # i18n-allow
    "When a PR merges, ask Scout to summarize it",
    "Ogni mattina alle otto dammi un briefing",
    "Puoi riassumere le email ogni giorno?",
    "Ricordami ogni lunedì di pagare l'affitto",
    "Quando una PR viene unita, chiedi a Scout di riassumerla",
    "Non appena arriva un messaggio, avvisami",
    "Wenn eine PR gemergt wird, lass Scout sie zusammenfassen",  # i18n-allow
    "Cuando se cierre una PR, pide a Scout que la resuma",  # i18n-allow
]

NOT_ROUTINE = [
    "Ich gehe jeden Morgen joggen",  # i18n-allow
    "How do I get a report every day?",
    "Wie kann ich jeden Tag um 8 ein Briefing bekommen?",  # i18n-allow
    "Was steht heute im Kalender?",  # i18n-allow
    "Erstelle einen Skill, der mir jeden Morgen um 6 die Mails vorliest",  # i18n-allow
    "When a PR merges, the deployment starts",
    "How do I notify Scout when a PR merges?",
    "Ogni mattina vado a correre",
    "Come posso ricevere un briefing ogni giorno?",
    "Quando una PR viene unita, la pipeline parte",
    "Crea una skill che riassuma le email ogni mattina",
]


@pytest.mark.parametrize("text", ROUTINE_REQUESTS)
def test_scheduled_requests_belong_to_an_agent_routine(text: str) -> None:
    assert wants_agent_routine(text)


@pytest.mark.parametrize("text", NOT_ROUTINE)
def test_habits_questions_and_skill_requests_are_not_routines(text: str) -> None:
    assert not wants_agent_routine(text)


def test_recurrence_needs_a_request_not_a_description() -> None:
    assert requests_recurring_work("Please send me the news every evening")
    assert not requests_recurring_work("The newsletter arrives every evening")


def test_event_trigger_needs_an_event_first_command() -> None:
    assert requests_event_triggered_work(
        "When a PR merges, ask Scout to summarize it"
    )
    assert not requests_event_triggered_work(
        "When a PR merges, the deployment starts"
    )


@pytest.mark.parametrize("text", [
    "Ogni mattina alle otto dammi un briefing",
    "Quando una PR viene unita, chiedi a Scout di riassumerla",
])
def test_italian_requests_mandate_agent_chat_save(text: str) -> None:
    assert requests_routine_creation(text)


@pytest.mark.parametrize("text", [
    "Come posso ricevere un briefing ogni giorno?",
    "Quando una PR viene unita, la pipeline parte",
    "Crea una skill che riassuma le email ogni mattina",
])
def test_italian_non_requests_do_not_mandate_agent_chat_save(text: str) -> None:
    assert not requests_routine_creation(text)
