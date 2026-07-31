"""The planner becomes skill-aware without gaining IO or an import edge.

Realtime voice was completely skill-blind. ``turn_planner`` recognised only the
three literal trigger words in ``_SKILL_RE``, so an utterance naming an installed
skill produced no skill reason and never reached the orchestrator that could
run it. Meanwhile the live model in delegate mode sees exactly two function
declarations, neither of which is a skill.

Two properties matter here and they pull against each other:

* the planner must SEE installed skills, and
* it must keep its promise of no model call, no disk access, no network — which
  is why the index arrives as a frozen data snapshot through an optional
  parameter instead of being imported.
"""
from __future__ import annotations

import pytest

from jarvis.brain.turn_planner import TurnPath, TurnReason, plan_turn
from jarvis.skills.relevance import RelevanceRanking, ScoredSkill, build_index

# Speech input under test.
#: MEASURED to reach the FIRE band against the seven-skill fixture below.
#: Naming only one indexed term scores ~0.74 against a cut-off near 0.98, i.e.
#: NARROW — which is correct behaviour, so the fixture names both.
FOCUS_UTTERANCE = "aktiviere den fokus und den konzentrationsmodus"  # i18n-allow
WEATHER_UTTERANCE = "wie ist das wetter heute in berlin"  # i18n-allow
LITERAL_SKILL_WORD = "welche skills habe ich installiert"  # i18n-allow


class _FakeFrontmatter:
    def __init__(self, tags: list[str]) -> None:
        self.description = "A focus mode."
        self.when_to_use = ""
        self.category = "productivity"
        self.tags = tags
        self.triggers: list[object] = []
        self.intent_verbs: list[str] = []
        self.intent_objects: list[str] = []


class _FakeSkill:
    def __init__(self, name: str, tags: list[str]) -> None:
        self.name = name
        self.frontmatter = _FakeFrontmatter(tags)


def _index() -> object:
    skills = [_FakeSkill("deep-work-mode", ["konzentrationsmodus", "fokus"])]  # i18n-allow
    skills += [_FakeSkill(f"filler-{i}", [f"topic{i}"]) for i in range(6)]
    return build_index(skills)


# ---------------------------------------------------------------------------
# Backwards compatibility: the parameter is optional and inert
# ---------------------------------------------------------------------------


def test_the_planner_still_works_without_an_index() -> None:
    """Every existing caller passes nothing — behaviour must be unchanged."""
    plan = plan_turn(WEATHER_UTTERANCE)
    assert TurnReason.SKILL not in plan.reasons


def test_an_explicit_none_index_is_the_same_as_omitting_it() -> None:
    assert plan_turn(FOCUS_UTTERANCE, skill_index=None).reasons == plan_turn(
        FOCUS_UTTERANCE
    ).reasons


def test_the_literal_skill_word_still_works_on_its_own() -> None:
    """The static vocabulary branch is untouched."""
    plan = plan_turn(LITERAL_SKILL_WORD)
    assert TurnReason.SKILL in plan.reasons


# ---------------------------------------------------------------------------
# With an index: content-aware detection
# ---------------------------------------------------------------------------


def test_an_installed_skill_is_now_recognised() -> None:
    """The gap this closes. Without the index this utterance produced no skill
    reason at all, so realtime never routed it anywhere that could run it."""
    plan = plan_turn(FOCUS_UTTERANCE, skill_index=_index())
    assert TurnReason.SKILL in plan.reasons
    assert plan.path is TurnPath.ORCHESTRATOR
    assert any(name.startswith("skill:") for name in plan.required_capabilities)


def test_an_unrelated_utterance_gains_no_skill_reason() -> None:
    plan = plan_turn(WEATHER_UTTERANCE, skill_index=_index())
    assert TurnReason.SKILL not in plan.reasons


def test_a_definitional_question_is_not_a_skill_turn() -> None:
    """Asking what something IS must never route as wanting it DONE."""
    plan = plan_turn(
        "was ist eigentlich ein konzentrationsmodus",  # i18n-allow
        skill_index=_index(),
    )
    assert TurnReason.SKILL not in plan.reasons


def test_only_a_fire_band_hit_counts() -> None:
    """A NARROW candidate is prompt material, not a reason to pay a delegation.

    A realtime delegation costs seconds of silence (BUG-087 measured 9.6 s to
    first audio), so a weak signal must not buy one.
    """

    class _NarrowIndex:
        fire_threshold = 10.0

        def rank(self, text: str, *, limit: int = 5) -> RelevanceRanking:
            return RelevanceRanking(
                ranked=(ScoredSkill(name="deep-work-mode", score=0.5),),
                clear_winner=True,
                fire_threshold=10.0,
                hint_threshold=0.1,
            )

    plan = plan_turn(FOCUS_UTTERANCE, skill_index=_NarrowIndex())
    assert TurnReason.SKILL not in plan.reasons


def test_an_ambiguous_winner_does_not_count() -> None:
    """Two near-tied skills are ambiguity, not a decision."""

    class _TiedIndex:
        def rank(self, text: str, *, limit: int = 5) -> RelevanceRanking:
            return RelevanceRanking(
                ranked=(
                    ScoredSkill(name="plugin-slack", score=2.0),
                    ScoredSkill(name="plugin-discord", score=1.95),
                ),
                clear_winner=False,
                fire_threshold=1.0,
                hint_threshold=0.3,
            )

    plan = plan_turn("schick eine nachricht", skill_index=_TiedIndex())  # i18n-allow
    assert TurnReason.SKILL not in plan.reasons


# ---------------------------------------------------------------------------
# Purity: no IO, no import edge, no crash
# ---------------------------------------------------------------------------


def test_a_broken_index_degrades_to_the_static_vocabulary() -> None:
    """A scorer fault must never break a turn."""

    class _ExplodingIndex:
        def rank(self, text: str, *, limit: int = 5) -> RelevanceRanking:
            raise RuntimeError("index is on fire")

    plan = plan_turn(FOCUS_UTTERANCE, skill_index=_ExplodingIndex())
    assert TurnReason.SKILL not in plan.reasons
    # And the rest of the planning still happened.
    assert isinstance(plan.path, TurnPath)


def test_the_planner_module_imports_nothing_from_jarvis_skills() -> None:
    """The purity promise, checked structurally.

    ``jarvis/skills/__init__.py`` is a heavy eager package init (registry,
    runner, watchdog, pydantic). A module-scope import here would drag all of it
    into every early import of the planner (AP-26) — which is exactly why the
    index is passed in as data.
    """
    from pathlib import Path

    import jarvis.brain.turn_planner as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "from jarvis.skills" not in code
    assert "import jarvis.skills" not in code


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_input_is_still_a_native_turn(text: str) -> None:
    assert plan_turn(text, skill_index=_index()).path is TurnPath.NATIVE_REALTIME
