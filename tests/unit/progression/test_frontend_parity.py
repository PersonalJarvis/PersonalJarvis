"""The Verse's copy of the level-system ids matches the Python rulebook (AP-4).

``levelCatalog.ts`` only knows what each id looks like; the server owns the
numbers. A reward, title, source or slot added on one side without the other
would render nothing or show a raw key, so the lists must be equal.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jarvis.progression.rules import (
    LOOK_UNLOCKS,
    REWARDS,
    RULES,
    SLOTS,
    SUBJECT_KINDS,
    TITLES,
    WORLD_ACTIONS,
)

_FRONTEND = Path(__file__).resolve().parents[3] / "jarvis/ui/web/frontend/src"
_CATALOG = _FRONTEND / "components/society/progression/levelCatalog.ts"
_LOCALES = _FRONTEND / "i18n/locales/society"


def _ts_list(name: str) -> list[str]:
    text = _CATALOG.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", text, re.S)
    assert match, f"{name} not found in levelCatalog.ts"
    return re.findall(r'"([a-z_]+)"', match.group(1))


def _ts_union(name: str) -> set[str]:
    text = _CATALOG.read_text(encoding="utf-8")
    match = re.search(rf"export type {name} =(.*?);", text, re.S)
    assert match, f"{name} not found in levelCatalog.ts"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_kinds_slots_rewards_titles_and_sources_match():
    assert _ts_list("SUBJECT_KINDS") == list(SUBJECT_KINDS)
    assert _ts_list("SLOTS") == list(SLOTS)
    assert _ts_list("REWARD_IDS") == [r.reward_id for r in REWARDS]
    assert set(_ts_list("TITLE_IDS")) == {t for bands in TITLES.values() for _, t in bands}
    assert _ts_list("XP_SOURCES") == [r.source for r in RULES]
    assert _ts_union("WorldAction") == set(WORLD_ACTIONS)


def test_every_id_has_a_label_in_every_language():
    titles = {t for bands in TITLES.values() for _, t in bands}
    for lang in ("en", "de", "es"):
        chunk = json.loads((_LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
        level = chunk["society"]["level"]
        assert set(level["reward"]) == {r.reward_id for r in REWARDS}, lang
        assert set(level["title"]) == titles, lang
        assert set(level["source"]) == {r.source for r in RULES}, lang
        assert set(level["slot"]) == set(SLOTS), lang
        for kind in SUBJECT_KINDS:
            assert f"rules_{kind}" in level, (lang, kind)


def test_every_agent_look_is_a_real_accessory():
    catalog = json.loads(
        (_FRONTEND / "components/society/companion/accessories.json").read_text(encoding="utf-8")
    )
    assert set(LOOK_UNLOCKS) == {item["id"] for item in catalog["items"]}
