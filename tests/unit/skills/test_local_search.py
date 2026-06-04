"""Regression + behaviour tests for jarvis.skills.local_search.

BUG-D: the skills route imported ``jarvis.skills.local_search`` but the module
was never committed, so ``POST /api/skills/query`` returned HTTP 500 on every
Builtin/Meine/category/text filter. The import test below is the guard that
would have caught that; the rest pin the filter + ranking behaviour.
"""
from __future__ import annotations

import pytest


def test_module_imports() -> None:
    """The route imports these names lazily — they must exist (BUG-D guard)."""
    from jarvis.skills.local_search import LocalSearchFilters, LocalSkillSearch

    assert LocalSearchFilters is not None
    assert LocalSkillSearch is not None


class _FakeRiskPolicy:
    def __init__(self, tier: str = "monitor") -> None:
        self.default_tier = tier


class _FakeFrontmatter:
    def __init__(
        self,
        category: str = "general",
        description: str = "",
        tags: list[str] | None = None,
        tier: str = "monitor",
    ) -> None:
        self.category = category
        self.description = description
        self.tags = tags or []
        self.risk_policy = _FakeRiskPolicy(tier)


class _FakeState:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeSkill:
    def __init__(self, name: str, state: str = "active", frontmatter=None) -> None:
        self.name = name
        self.state = _FakeState(state)
        self.frontmatter = frontmatter


class _FakeRegistry:
    def __init__(self, skills: list[_FakeSkill]) -> None:
        self._skills = skills

    def list(self) -> list[_FakeSkill]:
        return list(self._skills)


def _registry() -> _FakeRegistry:
    return _FakeRegistry(
        [
            _FakeSkill(
                "morning-routine",
                "active",
                _FakeFrontmatter("productivity", "Calendar briefing and weather", ["calendar"]),
            ),
            _FakeSkill(
                "skill-creator",
                "active",
                _FakeFrontmatter("meta", "Create and improve skills", ["meta"]),
            ),
            _FakeSkill("broken-draft", "draft", None),  # no frontmatter
        ]
    )


@pytest.mark.asyncio
async def test_empty_query_is_filter_router() -> None:
    from jarvis.skills.local_search import LocalSearchFilters, LocalSkillSearch

    s = LocalSkillSearch(registry=_registry(), brain=None)
    hits, brain_used = await s.query(LocalSearchFilters(q="", limit=30))

    assert {h.name for h in hits} == {"morning-routine", "skill-creator", "broken-draft"}
    assert brain_used is False


@pytest.mark.asyncio
async def test_query_ranks_and_drops_non_matches() -> None:
    from jarvis.skills.local_search import LocalSearchFilters, LocalSkillSearch

    s = LocalSkillSearch(registry=_registry(), brain=None)
    hits, _ = await s.query(LocalSearchFilters(q="calendar", limit=30))

    assert [h.name for h in hits] == ["morning-routine"]
    assert hits[0].score > 0
    assert "tag" in hits[0].reason or "description" in hits[0].reason


@pytest.mark.asyncio
async def test_state_filter() -> None:
    from jarvis.skills.local_search import LocalSearchFilters, LocalSkillSearch

    s = LocalSkillSearch(registry=_registry(), brain=None)
    hits, _ = await s.query(LocalSearchFilters(q="", state="draft", limit=30))

    assert [h.name for h in hits] == ["broken-draft"]


@pytest.mark.asyncio
async def test_category_filter_skips_draft_without_frontmatter() -> None:
    """A category filter must not crash on DRAFT skills (frontmatter=None)."""
    from jarvis.skills.local_search import LocalSearchFilters, LocalSkillSearch

    s = LocalSkillSearch(registry=_registry(), brain=None)
    hits, _ = await s.query(LocalSearchFilters(q="", category="meta", limit=30))

    assert [h.name for h in hits] == ["skill-creator"]


@pytest.mark.asyncio
async def test_limit_is_applied() -> None:
    from jarvis.skills.local_search import LocalSearchFilters, LocalSkillSearch

    s = LocalSkillSearch(registry=_registry(), brain=None)
    hits, _ = await s.query(LocalSearchFilters(q="", limit=1))

    assert len(hits) == 1
