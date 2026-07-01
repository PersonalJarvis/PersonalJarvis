"""Curated seed catalog for the skill finder.

The catalog is a static JSON file with hand-maintained entries from:
- Anthropic's official skills repo (https://github.com/anthropics/skills)
- skills.sh (community aggregator)
- GitHub repos with a verified skill format (SKILL.md + YAML frontmatter)

The catalog is not a runtime source of truth — it's the seed that the
``SkillFinder`` hands to the brain as a candidate pool. The brain ranks and
filters based on the user query + trust/category/risk preferences.

Extending it:
- By hand: add new entries to ``seed_catalog.json``.
- Future: a scraper job that periodically re-reads skills.sh + GitHub and
  replaces the JSON (similar to rebuilding a package index).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).parent / "seed_catalog.json"


@lru_cache(maxsize=1)
def load_catalog() -> list[dict[str, Any]]:
    """Loads the entries from the seed catalog.

    Cached for the process lifetime — the catalog is static, and we don't
    want to re-parse the JSON on every request. On a hot swap (e.g. when a
    scraper replaces the file), ``load_catalog.cache_clear()`` must be called.
    """
    if not CATALOG_PATH.exists():
        return []
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    return [dict(e) for e in entries]


__all__ = ["load_catalog", "CATALOG_PATH"]
