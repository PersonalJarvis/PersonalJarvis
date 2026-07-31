"""One scorer, two surfaces — pinned.

The UI search box and the voice matcher used to rank skills with two independent
implementations. That drift is invisible while it happens and only shows up as a
confusing product: the box finds a skill the assistant never will, or the
reverse, with nothing to catch either.

The old local scorer also had strictly less to work with — name, tags, category
and description only. It had no channel for the trigger literals, ``when_to_use``
or ``intent_objects`` where the German and Spanish vocabulary actually lives, so
searching for a trigger word a skill genuinely declares found nothing.

These tests pin the unification and the two differences that are deliberate:
the UI must see inactive skills (you cannot promote a draft you cannot find),
and the structural sidebar filters stay UI-only.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.skills.local_search import LocalSearchFilters, LocalSkillSearch
from jarvis.skills.relevance import build_index
from jarvis.skills.schema import SkillLifecycleState

# Speech input under test.
TRIGGER_WORD = "fokusmodus"  # i18n-allow
INBOX_WORD = "posteingang"  # i18n-allow


class _FakeRiskPolicy:
    def __init__(self, tier: str = "monitor") -> None:
        self.default_tier = tier


class _FakeFrontmatter:
    def __init__(
        self,
        *,
        category: str = "general",
        description: str = "",
        when_to_use: str = "",
        tags: list[str] | None = None,
        tier: str = "monitor",
        triggers: list[Any] | None = None,
        intent_objects: list[str] | None = None,
    ) -> None:
        self.category = category
        self.description = description
        self.when_to_use = when_to_use
        self.tags = tags or []
        self.risk_policy = _FakeRiskPolicy(tier)
        self.triggers = triggers or []
        self.intent_verbs: list[str] = []
        self.intent_objects = intent_objects or []


class _FakeTrigger:
    def __init__(self, pattern: str) -> None:
        self.type = "voice"
        self.pattern = pattern


class _FakeSkill:
    def __init__(
        self,
        name: str,
        frontmatter: _FakeFrontmatter | None = None,
        state: SkillLifecycleState = SkillLifecycleState.ACTIVE,
    ) -> None:
        self.name = name
        self.frontmatter = frontmatter
        self.state = state


class _FakeRegistry:
    def __init__(self, skills: list[_FakeSkill]) -> None:
        self._skills = skills
        self.generation = 1

    def list(self) -> list[_FakeSkill]:
        return list(self._skills)

    def list_active(self) -> list[_FakeSkill]:
        return [
            s
            for s in self._skills
            if s.state in (SkillLifecycleState.ACTIVE, SkillLifecycleState.VALIDATED)
        ]


def _corpus() -> list[_FakeSkill]:
    skills = [
        _FakeSkill(
            "deep-work-mode",
            _FakeFrontmatter(
                category="productivity",
                description="A distraction-free focus sprint.",
                tags=["focus", "timer"],
                triggers=[_FakeTrigger(f"({TRIGGER_WORD})")],
            ),
        ),
        _FakeSkill(
            "plugin-gmail",
            _FakeFrontmatter(
                category="communication",
                description="Read and send email.",
                intent_objects=[INBOX_WORD, "inbox"],
            ),
        ),
    ]
    skills += [
        _FakeSkill(
            f"filler-{index}",
            _FakeFrontmatter(description=f"topic{index} placeholder"),
        )
        for index in range(6)
    ]
    return skills


# ---------------------------------------------------------------------------
# The parity contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [TRIGGER_WORD, INBOX_WORD, "focus sprint", "email", "topic3"],
)
async def test_ui_search_scores_match_the_shared_scorer(query: str) -> None:
    """Same corpus, same query, identical scores.

    Without this, the two surfaces re-diverge within a quarter and nothing
    reports it.
    """
    skills = _corpus()
    searcher = LocalSkillSearch(_FakeRegistry(skills))
    hits, brain_used = await searcher.query(LocalSearchFilters(q=query, limit=10))
    assert brain_used is False

    expected = build_index(skills).rank(query, limit=10)
    assert [h.name for h in hits] == [s.name for s in expected.ranked]
    assert [h.score for h in hits] == [round(s.score, 6) for s in expected.ranked]


async def test_a_trigger_literal_is_now_findable_in_the_ui() -> None:
    """The concrete regression the old scorer had.

    ``fokusmodus`` exists only inside the skill's trigger regex — a field the
    previous local scorer never looked at, so the search box could not find it.
    """
    searcher = LocalSkillSearch(_FakeRegistry(_corpus()))
    hits, _ = await searcher.query(LocalSearchFilters(q=TRIGGER_WORD))
    assert hits
    assert hits[0].name == "deep-work-mode"


async def test_intent_objects_are_findable_too() -> None:
    searcher = LocalSkillSearch(_FakeRegistry(_corpus()))
    hits, _ = await searcher.query(LocalSearchFilters(q=INBOX_WORD))
    assert hits
    assert hits[0].name == "plugin-gmail"


async def test_the_reason_names_the_field_that_matched() -> None:
    """"matched name, description" told nobody anything."""
    searcher = LocalSkillSearch(_FakeRegistry(_corpus()))
    hits, _ = await searcher.query(LocalSearchFilters(q=TRIGGER_WORD))
    assert ":" in hits[0].reason


# ---------------------------------------------------------------------------
# The deliberate differences
# ---------------------------------------------------------------------------


async def test_the_ui_can_find_a_draft_so_it_can_be_promoted() -> None:
    """AP-15 excludes drafts from the VOICE index, never from the UI."""
    skills = _corpus()
    skills.append(
        _FakeSkill(
            "secret-draft",
            _FakeFrontmatter(tags=["draftonlyterm"]),
            state=SkillLifecycleState.DRAFT,
        )
    )
    searcher = LocalSkillSearch(_FakeRegistry(skills))
    hits, _ = await searcher.query(LocalSearchFilters(q="draftonlyterm"))
    assert [h.name for h in hits] == ["secret-draft"]


async def test_structural_filters_still_narrow_the_result() -> None:
    searcher = LocalSkillSearch(_FakeRegistry(_corpus()))
    hits, _ = await searcher.query(
        LocalSearchFilters(q="", category="communication", limit=10)
    )
    assert [h.name for h in hits] == ["plugin-gmail"]


async def test_a_filter_narrows_the_ranked_corpus_too() -> None:
    """Ranking happens over the FILTERED set, so IDF reflects what is searched."""
    searcher = LocalSkillSearch(_FakeRegistry(_corpus()))
    hits, _ = await searcher.query(
        LocalSearchFilters(q="focus", category="communication", limit=10)
    )
    assert all(h.name == "plugin-gmail" for h in hits)


async def test_an_empty_query_is_deterministic_by_name() -> None:
    searcher = LocalSkillSearch(_FakeRegistry(_corpus()))
    hits, _ = await searcher.query(LocalSearchFilters(q="", limit=50))
    names = [h.name for h in hits]
    assert names == sorted(names, key=str.lower)


async def test_the_limit_is_respected() -> None:
    searcher = LocalSkillSearch(_FakeRegistry(_corpus()))
    hits, _ = await searcher.query(LocalSearchFilters(q="", limit=3))
    assert len(hits) == 3


async def test_a_broken_registry_returns_nothing_instead_of_raising() -> None:
    class _Exploding:
        def list(self) -> list[_FakeSkill]:
            raise RuntimeError("registry is on fire")

    searcher = LocalSkillSearch(_Exploding())
    hits, brain_used = await searcher.query(LocalSearchFilters(q="anything"))
    assert hits == []
    assert brain_used is False


async def test_a_skill_without_frontmatter_is_still_searchable_by_name() -> None:
    skills = _corpus()
    skills.append(_FakeSkill("browser-tabs", None))
    searcher = LocalSkillSearch(_FakeRegistry(skills))
    hits, _ = await searcher.query(LocalSearchFilters(q="browser tabs"))
    assert hits
    assert hits[0].name == "browser-tabs"


def test_local_search_owns_no_scoring_function() -> None:
    """Structural guard: a second scorer must not creep back in.

    The module may filter and join; the moment it starts weighting fields again,
    the two surfaces are free to diverge.
    """
    from pathlib import Path

    import jarvis.skills.local_search as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "_WEIGHT_" not in code
    assert "def _score" not in code
