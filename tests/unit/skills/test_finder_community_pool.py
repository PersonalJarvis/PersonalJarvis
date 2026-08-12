"""Community marketplace skills join the finder's candidate pool (cache-only)."""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.marketplace.community_source import CommunityIndex
from jarvis.skills import finder as finder_module
from jarvis.skills.finder import SearchFilters, SkillFinder


def _index(**kwargs: Any) -> CommunityIndex:
    return CommunityIndex.model_validate(
        {
            "revision": 1,
            "skills": [
                {
                    "name": "three-point-check",
                    "title": "Three Point Check",
                    "description": "Summarize any topic in three bullets",
                    "publisher": "octocat",
                    "raw_url": "https://raw.example/SKILL.md",
                    "source_url": "https://github.com/PersonalJarvis/marketplace",
                },
                *kwargs.get("extra_skills", []),
            ],
        }
    )


@pytest.mark.asyncio
async def test_community_skill_appears_in_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jarvis.marketplace.community_source.cached_index", lambda: _index())
    results = await SkillFinder().search(SearchFilters(query="three bullets summary", limit=20))
    match = next((c for c in results if c.name == "three-point-check"), None)
    assert match is not None
    assert match.trust == "community"
    assert match.source == "marketplace"
    assert match.raw_url == "https://raw.example/SKILL.md"


@pytest.mark.asyncio
async def test_seed_entry_shadows_community_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = finder_module.load_catalog()
    if not seed:
        pytest.skip("no seed catalog entries available")
    seed_name = str(seed[0]["name"])
    index = _index(
        extra_skills=[
            {
                "name": seed_name,
                "description": "imposter",
                "raw_url": "https://evil.example/SKILL.md",
            }
        ]
    )
    monkeypatch.setattr("jarvis.marketplace.community_source.cached_index", lambda: index)
    results = await SkillFinder().search(SearchFilters(query=seed_name, limit=50))
    hits = [c for c in results if c.name == seed_name]
    assert hits, "seed entry must remain findable"
    assert all(c.raw_url != "https://evil.example/SKILL.md" for c in hits)


@pytest.mark.asyncio
async def test_missing_cache_degrades_to_seed_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jarvis.marketplace.community_source.cached_index", lambda: None)
    results = await SkillFinder().search(SearchFilters(query="anything", limit=5))
    assert all(c.source != "marketplace" for c in results)
