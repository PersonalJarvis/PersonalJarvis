"""Kuratierter Seed-Katalog fuer den Skill-Finder.

Der Katalog ist eine statische JSON-Datei mit handgepflegten Eintraegen aus:
- Anthropic's offiziellem Skills-Repo (https://github.com/anthropics/skills)
- skills.sh (Community-Aggregator)
- GitHub-Repos mit nachweislichem Skill-Format (SKILL.md + YAML-Frontmatter)

Der Katalog ist keine Runtime-Source-of-Truth — er ist der Seed, den der
``SkillFinder`` dem Brain als Kandidaten-Pool uebergibt. Das Brain rankt und
filtert basierend auf User-Query + Trust/Category/Risk-Preferences.

Erweiterung:
- Hand: Neue Eintraege in ``seed_catalog.json`` ergaenzen.
- Zukunft: Scraper-Job, der skills.sh + GitHub periodisch neu einliest und
  die JSON ersetzt (aehnlich wie man eine Package-Index neu baut).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).parent / "seed_catalog.json"


@lru_cache(maxsize=1)
def load_catalog() -> list[dict[str, Any]]:
    """Laedt die Eintraege aus dem Seed-Katalog.

    Cached fuer den Prozess-Lifetime — der Katalog ist statisch, und wir wollen
    nicht bei jedem Request die JSON neu parsen. Bei Hot-Swap (z.B. wenn ein
    Scraper die Datei ersetzt) muss ``load_catalog.cache_clear()`` gerufen werden.
    """
    if not CATALOG_PATH.exists():
        return []
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    return [dict(e) for e in entries]


__all__ = ["load_catalog", "CATALOG_PATH"]
