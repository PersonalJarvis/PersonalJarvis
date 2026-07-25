"""Contract tests for jarvis.skills.match_eval.

``evaluate_match`` is the ONE entry point the brain, the Test-Match route and
the offline eval all call. These tests pin the two properties that make sharing
it safe: it never raises (a broken skill subsystem must degrade to "nothing
matched", never break a turn) and the author's regex triggers keep absolute
precedence over anything added later.

At this phase the relevance fallback is not wired yet, so a trigger miss is a
total miss — exactly today's behaviour. That is deliberate: the offline eval
must be able to baseline the CURRENT system, including its near-zero
paraphrase recall, before the new algorithm exists. Without that baseline there
is nothing to compare against and a recall regression is invisible.
"""
from __future__ import annotations

import pytest

from jarvis.skills import match_eval
from jarvis.skills.schema import SkillLifecycleState

# ---------------------------------------------------------------------------
# Fakes shaped for TriggerMatcher (which reads real lifecycle enums)
# ---------------------------------------------------------------------------


class _FakeTrigger:
    def __init__(
        self, pattern: str, kind: str = "voice", language: list[str] | None = None
    ) -> None:
        self.type = kind
        self.pattern = pattern
        self.language = language or []
        self.combo = None
        self.cron = None


class _FakeFrontmatter:
    def __init__(self, triggers: list[_FakeTrigger]) -> None:
        self.triggers = triggers


class _FakeSkill:
    def __init__(
        self,
        name: str,
        patterns: list[str],
        state: SkillLifecycleState = SkillLifecycleState.ACTIVE,
    ) -> None:
        self.name = name
        self.state = state
        self.frontmatter = _FakeFrontmatter([_FakeTrigger(p) for p in patterns])


class _FakeRegistry:
    def __init__(self, skills: list[_FakeSkill]) -> None:
        self._skills = skills

    def list(self) -> list[_FakeSkill]:
        return list(self._skills)

    def by_trigger(self, kind: str) -> list[_FakeSkill]:
        return [
            s
            for s in self._skills
            if any(t.type == kind for t in s.frontmatter.triggers)
        ]


class _ExplodingRegistry:
    def by_trigger(self, kind: str) -> list[_FakeSkill]:
        raise RuntimeError("registry is on fire")

    def list(self) -> list[_FakeSkill]:
        raise RuntimeError("registry is on fire")


# Trigger vocabulary under test — mirrors the real builtin patterns.
_MORNING_PATTERN = (
    r"(morgenroutine|morning routine|tages(ue|ü)berblick)"  # i18n-allow: speech input
)
_FOCUS_PATTERN = r"(fokusmodus|deep work mode)"  # i18n-allow: speech input


def _registry() -> _FakeRegistry:
    return _FakeRegistry(
        [
            _FakeSkill("morning-routine", [_MORNING_PATTERN]),
            _FakeSkill("deep-work-mode", [_FOCUS_PATTERN]),
        ]
    )


# ---------------------------------------------------------------------------
# Band vocabulary
# ---------------------------------------------------------------------------


def test_bands_are_ordered_strongest_first() -> None:
    assert match_eval.MATCH_BANDS == ("fire", "narrow", "none")


@pytest.mark.parametrize(
    ("band", "floor", "expected"),
    [
        ("fire", "fire", True),
        ("fire", "narrow", True),
        ("narrow", "fire", False),
        ("narrow", "narrow", True),
        ("none", "narrow", False),
        ("bogus", "narrow", False),  # unknown fails closed
    ],
)
def test_band_at_least(band: str, floor: str, expected: bool) -> None:
    assert match_eval.band_at_least(band, floor) is expected


# ---------------------------------------------------------------------------
# Trigger path
# ---------------------------------------------------------------------------


def test_a_voice_trigger_fires() -> None:
    decision = match_eval.evaluate_match(_registry(), "starte die morgenroutine")
    assert decision.band == match_eval.BAND_FIRE
    assert decision.source == match_eval.SOURCE_TRIGGER
    assert decision.top is not None
    assert decision.top.skill_name == "morning-routine"
    assert decision.fired is True


def test_the_decision_carries_the_raw_matched_span() -> None:
    """``evidence`` feeds the definitional guard, which re-escapes it against
    the ORIGINAL text — so it must be the untouched span, not a normalized one."""
    spoken = "gib mir den Tagesüberblick"  # i18n-allow: speech input
    decision = match_eval.evaluate_match(_registry(), spoken)
    assert decision.top is not None
    assert decision.top.evidence == "Tagesüberblick"  # i18n-allow: speech input


def test_a_draft_skill_never_fires() -> None:
    """AP-15 — enforced by TriggerMatcher, pinned here at the shared entry point."""
    registry = _FakeRegistry(
        [
            _FakeSkill(
                "morning-routine",
                [r"morgenroutine"],
                state=SkillLifecycleState.DRAFT,
            )
        ]
    )
    assert match_eval.evaluate_match(registry, "starte die morgenroutine").band == (
        match_eval.BAND_NONE
    )


def test_a_disabled_skill_never_fires() -> None:
    registry = _FakeRegistry(
        [
            _FakeSkill(
                "deep-work-mode",
                [r"fokusmodus"],
                state=SkillLifecycleState.DISABLED,
            )
        ]
    )
    assert match_eval.evaluate_match(registry, "fokusmodus").band == (
        match_eval.BAND_NONE
    )


# ---------------------------------------------------------------------------
# Current-behaviour baseline: a paraphrase misses (this is the bug)
# ---------------------------------------------------------------------------


def test_a_natural_paraphrase_still_misses_at_this_phase() -> None:
    """Documents the gap the relevance layer closes.

    "ich brauch jetzt ruhe zum arbeiten" is unmistakably deep-work-mode to a
    human and matches none of its regexes. When the relevance fallback lands,
    THIS is the test that flips — and the offline eval measures how far.
    """
    decision = match_eval.evaluate_match(
        _registry(), "ich brauch jetzt ruhe zum arbeiten"
    )
    assert decision.band == match_eval.BAND_NONE
    assert decision.top is None


# ---------------------------------------------------------------------------
# Degradation — matching must never break a turn
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_input_is_a_clean_miss(text: str) -> None:
    assert match_eval.evaluate_match(_registry(), text).band == match_eval.BAND_NONE


def test_missing_registry_is_a_clean_miss() -> None:
    assert match_eval.evaluate_match(None, "morgenroutine").band == (
        match_eval.BAND_NONE
    )


def test_a_broken_registry_degrades_instead_of_raising() -> None:
    decision = match_eval.evaluate_match(_ExplodingRegistry(), "morgenroutine")
    assert decision.band == match_eval.BAND_NONE
    assert decision.top is None


def test_an_invalid_regex_does_not_break_the_evaluation() -> None:
    registry = _FakeRegistry(
        [
            _FakeSkill("broken", [r"([unclosed"]),
            _FakeSkill("deep-work-mode", [r"fokusmodus"]),
        ]
    )
    decision = match_eval.evaluate_match(registry, "fokusmodus")
    assert decision.top is not None
    assert decision.top.skill_name == "deep-work-mode"


def test_elapsed_is_recorded_for_every_decision() -> None:
    """The decision log needs a cost number even on a miss."""
    hit = match_eval.evaluate_match(_registry(), "fokusmodus")
    miss = match_eval.evaluate_match(_registry(), "wie ist das wetter")  # i18n-allow: speech input
    assert hit.elapsed_us >= 0
    assert miss.elapsed_us >= 0


def test_records_are_frozen() -> None:
    """Decisions travel onto the bus and into the ring buffer — immutability
    keeps a subscriber from mutating another's copy (bus event contract)."""
    decision = match_eval.evaluate_match(_registry(), "fokusmodus")
    with pytest.raises((AttributeError, TypeError)):
        decision.band = "fire"  # type: ignore[misc]
    assert decision.top is not None
    with pytest.raises((AttributeError, TypeError)):
        decision.top.score = 9.0  # type: ignore[misc]
