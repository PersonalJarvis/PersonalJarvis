"""Recurring work is recognised without the word "routine", and a request that
names a skill keeps its skill meaning (live 2026-09-29: "every day at 8"
ended as an inactive draft skill)."""

from __future__ import annotations

import pytest

from jarvis.society.routine_intent import requests_recurring_work, wants_agent_routine

ROUTINE_REQUESTS = [
    "Erstelle einen Agenten, der mir jeden Tag um 8 Uhr ein Morgenbriefing gibt",  # i18n-allow
    "gib mir jeden Morgen um acht ein Briefing",  # i18n-allow
    "Kannst du mir täglich meine wichtigsten Gmails zusammenfassen",  # i18n-allow
    "every day at 8 give me a morning briefing",
    "Remind me every Monday to pay rent",
    "dame un resumen cada mañana a las ocho",  # i18n-allow
    "Erstelle eine Routine für den Gmail Agenten",  # i18n-allow
]

NOT_ROUTINE = [
    "Ich gehe jeden Morgen joggen",  # i18n-allow
    "How do I get a report every day?",
    "Wie kann ich jeden Tag um 8 ein Briefing bekommen?",  # i18n-allow
    "Was steht heute im Kalender?",  # i18n-allow
    "Erstelle einen Skill, der mir jeden Morgen um 6 die Mails vorliest",  # i18n-allow
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
