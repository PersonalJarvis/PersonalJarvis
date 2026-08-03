"""Five-layer drift guard for word search (AP-4 / BUG-008).

Two things cross Python → REST → TypeScript → i18n → UI here, and both fail
SILENTLY when one hop is forgotten:

* ``WordSearchStatus`` — a status the frontend does not know renders as a
  blank panel, which is the precise failure the named statuses exist to end.
* ``SearchResult``'s passage provenance — the whole point of the feature is
  that a hit says WHICH passage answered, and a field added on one side and
  forgotten on the other quietly turns that back into "somewhere in this file".

The retrieval leg names are pinned too: the panel labels them from a lookup
map, and ``t()`` echoes an unknown key back verbatim, so a leg renamed in
Python would render as a raw i18n key on screen.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from jarvis.ultrawiki.types import SearchResult, WordSearchStatus

FRONTEND = Path(__file__).resolve().parents[3] / "jarvis/ui/web/frontend/src"
API_TS = FRONTEND / "lib/ultrawikiApi.ts"
PANEL_TSX = FRONTEND / "components/ultrawiki/WordSearchPanel.tsx"
LOCALES = FRONTEND / "i18n/locales"
SUPPORTED_LOCALES = ("de", "en", "es")


def _ts_union_values(name: str) -> set[str]:
    source = API_TS.read_text(encoding="utf-8")
    match = re.search(rf"{name}\s*=\s*\[(.*?)\]\s*as const", source, re.DOTALL)
    assert match is not None, f"{name} array not found"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _locale(name: str) -> dict:
    return json.loads((LOCALES / f"{name}.json").read_text(encoding="utf-8"))


def _nested(data: dict, dotted: str) -> object | None:
    node: object = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def test_typescript_status_union_matches_the_python_enum():
    assert _ts_union_values("ULTRAWIKI_WORD_SEARCH_STATUSES") == {
        status.value for status in WordSearchStatus
    }


def test_the_search_hit_interface_carries_every_python_field():
    """``dataclasses.asdict`` puts EVERY field on the wire, so a field added in
    Python without its TypeScript twin is invisible to the UI rather than a
    type error anyone would notice."""
    source = API_TS.read_text(encoding="utf-8")
    match = re.search(
        r"export interface UltraWikiSearchHit \{(.*?)\n\}", source, re.DOTALL
    )
    assert match is not None, "UltraWikiSearchHit interface not found"
    declared = set(re.findall(r"^\s*(\w+)[?]?:", match.group(1), re.MULTILINE))
    expected = {field.name for field in dataclasses.fields(SearchResult)}
    assert expected - declared == set(), (
        f"UltraWikiSearchHit is missing: {sorted(expected - declared)}"
    )


def test_every_retrieval_leg_has_a_label_in_every_locale():
    """The legs of ``word_search._retrieve``, as the panel labels them."""
    from jarvis.ultrawiki import word_search as word_search_mod

    source = Path(word_search_mod.__file__).read_text(encoding="utf-8")
    legs = set(re.findall(r'\(\s*"(\w+)",\s*(?:keyword|vector)_weight', source))
    legs |= set(re.findall(r'^\s+"(\w+)",\n\s+vector_weight', source, re.MULTILINE))
    assert legs, "no retrieval legs found — the extraction regex went stale"

    panel = PANEL_TSX.read_text(encoding="utf-8")
    mapped = set(re.findall(r'^\s*(\w+): "ultrawiki\.words\.leg_', panel, re.MULTILINE))
    assert legs <= mapped, f"WordSearchPanel does not label: {sorted(legs - mapped)}"
    for locale in SUPPORTED_LOCALES:
        data = _locale(locale)
        missing = sorted(
            leg
            for leg in legs
            if not isinstance(_nested(data, f"ultrawiki.words.leg_{leg}"), str)
        )
        assert not missing, f"{locale}.json has no label for legs: {missing}"


def test_every_string_the_words_view_asks_for_exists_in_every_locale():
    """A missing key renders as the raw dotted key on screen."""
    sources = [PANEL_TSX.read_text(encoding="utf-8")]
    sources.append(
        (FRONTEND / "views/ultrawiki/UltraWikiPanel.tsx").read_text(encoding="utf-8")
    )
    used: set[str] = set()
    for source in sources:
        used |= set(re.findall(r't\(\s*"(ultrawiki\.words\.[^"]+)"', source))
    assert used, "no i18n keys found — the extraction regex went stale"
    for locale in SUPPORTED_LOCALES:
        data = _locale(locale)
        missing = sorted(key for key in used if not isinstance(_nested(data, key), str))
        assert not missing, f"{locale}.json is missing: {missing}"


def test_the_words_locale_blocks_agree():
    """No locale may carry a word-search key the others lack."""
    blocks = {
        locale: _nested(_locale(locale), "ultrawiki.words")
        for locale in SUPPORTED_LOCALES
    }
    for locale, block in blocks.items():
        assert isinstance(block, dict) and block, f"{locale}.json has no words block"
    reference = set(blocks["en"])  # type: ignore[arg-type]
    for locale, block in blocks.items():
        assert set(block) == reference, (  # type: ignore[arg-type]
            f"{locale}.json word keys differ from en.json"
        )
