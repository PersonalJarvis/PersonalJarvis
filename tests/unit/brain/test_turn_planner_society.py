"""A named society agent, or a question about the team, needs the orchestrator.

The society twin of the workspace call-sign rule. "Gmail agent, check my
inbox" was routed natively (2026-09-03) because "Gmail agent" is a name the
user typed into the Agents section and no static vocabulary can hold it; the
live model then answered from its own knowledge. The roster is the evidence,
passed in like the call-signs so the planner keeps holding no registry.
"""

from __future__ import annotations

import pytest

from jarvis.brain.turn_planner import TurnPath, TurnReason, plan_turn

NAMES = ("Gmail agent", "Scout")


@pytest.mark.parametrize(
    "text",
    [
        "Gmail agent, check my inbox for the invoice.",
        "Was macht der Gmail Agent gerade?",  # i18n-allow: spoken input under test
        "Lass Scout das recherchieren.",  # i18n-allow: spoken input under test
        "Is Scout done yet?",
    ],
)
def test_a_named_agent_goes_to_the_orchestrator(text: str) -> None:
    plan = plan_turn(text, agent_names=NAMES)
    assert plan.path is TurnPath.ORCHESTRATOR
    assert TurnReason.SOCIETY in plan.reasons


@pytest.mark.parametrize(
    "text",
    [
        "Welche Agents hast du?",  # i18n-allow: spoken input under test
        "Welche Agenten stehen dir zur Verfügung?",  # i18n-allow: spoken input under test
        "Which agents do you have access to?",
        "How many agents are on the team?",
        "Delegate that to an agent.",
        "Gib das an einen Agenten.",  # i18n-allow: spoken input under test
        "¿Qué agentes tienes?",  # i18n-allow: spoken input under test
    ],
)
def test_a_question_about_the_team_needs_no_roster(text: str) -> None:
    plan = plan_turn(text)
    assert plan.path is TurnPath.ORCHESTRATOR
    assert TurnReason.SOCIETY in plan.reasons


@pytest.mark.parametrize(
    "text",
    [
        "My travel agent booked the flight, funny story.",
        "Wie geht es dir heute?",  # i18n-allow: spoken input under test
        "What is the capital of France?",
    ],
)
def test_the_word_agent_alone_is_not_the_society(text: str) -> None:
    plan = plan_turn(text, agent_names=NAMES)
    assert TurnReason.SOCIETY not in plan.reasons


def test_a_short_name_never_matches_inside_other_words() -> None:
    # "Al" would otherwise fire on "already"; names under three characters are skipped.
    plan = plan_turn("I already did that", agent_names=("Al",))
    assert TurnReason.SOCIETY not in plan.reasons
