"""A tie between two installed skills is still a skill turn.

Live forensic 2026-08-20. The user asked Jarvis to build a morning routine by
voice; the creator wrote one that overlapped the shipped ``morning-routine``.
From that moment both scored FIRE band on "Morgenroutine", the index reported
no clear winner, and the planner — which required one — stopped adding
``TurnReason.SKILL`` at all. Installing a second skill silently disabled
routing for the first.

The planner was answering "which skill?" when it had only been asked "is this
about a skill?". A tie is certainty about the first question and uncertainty
about the second, so the reason stands and only the ``skill:<name>`` pin drops.
"""
from __future__ import annotations

from dataclasses import dataclass

from jarvis.brain.turn_planner import TurnReason, plan_turn


@dataclass(frozen=True)
class _Candidate:
    name: str
    score: float


@dataclass(frozen=True)
class _Ranking:
    top: _Candidate | None
    fire_threshold: float
    clear_winner: bool


class _FakeIndex:
    """Stand-in for ``SkillMatchIndex`` — only ``rank()`` is read."""

    def __init__(self, ranking: _Ranking) -> None:
        self._ranking = ranking
        self.calls: list[str] = []

    def rank(self, text: str, limit: int = 1) -> _Ranking:
        self.calls.append(text)
        return self._ranking


_TIED = _Ranking(
    top=_Candidate(name="morning-routine", score=1.70),
    fire_threshold=1.18,
    clear_winner=False,
)
_CLEAR = _Ranking(
    top=_Candidate(name="morning-routine", score=1.83),
    fire_threshold=1.17,
    clear_winner=True,
)
_BELOW = _Ranking(
    top=_Candidate(name="morning-routine", score=0.40),
    fire_threshold=1.17,
    clear_winner=True,
)


def _pins(plan: object) -> list[str]:
    caps = getattr(plan, "required_capabilities", ())
    return [c for c in caps if str(c).startswith("skill:")]


def test_a_tie_between_two_skills_is_still_a_skill_turn() -> None:
    plan = plan_turn("Morgenroutine", skill_index=_FakeIndex(_TIED))  # i18n-allow: test input

    assert TurnReason.SKILL in plan.reasons
    assert plan.requires_orchestrator is True


def test_a_tie_does_not_pin_a_specific_skill() -> None:
    """Ambiguity travels to the orchestrator, which can disambiguate. The
    planner must not pick a winner it does not have."""
    plan = plan_turn("Morgenroutine", skill_index=_FakeIndex(_TIED))  # i18n-allow: test input

    assert _pins(plan) == []


def test_a_clear_winner_still_pins_its_skill() -> None:
    plan = plan_turn("Morgenroutine", skill_index=_FakeIndex(_CLEAR))  # i18n-allow: test input

    assert TurnReason.SKILL in plan.reasons
    assert _pins(plan) == ["skill:morning-routine"]


def test_a_below_threshold_candidate_is_not_a_skill_turn() -> None:
    """The FIRE floor still decides. A NARROW candidate is a suggestion for
    the orchestrator's prompt, never a reason to route."""
    plan = plan_turn("wie geht es dir", skill_index=_FakeIndex(_BELOW))  # i18n-allow: test input

    assert TurnReason.SKILL not in plan.reasons


def test_a_definitional_question_never_routes_to_a_skill() -> None:
    """"Was ist eine Morgenroutine?" is a question about the words, not an
    order to run the skill — the definition guard outranks any band."""
    plan = plan_turn(
        "Was ist eine Morgenroutine?",  # i18n-allow: test input
        skill_index=_FakeIndex(_TIED),
    )

    assert TurnReason.SKILL not in plan.reasons


def test_a_broken_index_falls_back_to_the_static_vocabulary() -> None:
    """A scorer fault must leave the planner exactly as it was before the
    index existed — never raise into a live turn."""

    class _Exploding:
        def rank(self, text: str, limit: int = 1):  # noqa: ANN202
            raise RuntimeError("index is corrupt")

    plan = plan_turn("Morgenroutine", skill_index=_Exploding())  # i18n-allow: test input

    assert TurnReason.SKILL not in plan.reasons
