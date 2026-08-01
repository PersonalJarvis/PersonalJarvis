"""The language bridge: a question in the user's language must reach a page
written in another one.

Measured 2026-07-25 against the maintainer's real vault: German questions
scored 0/7, the SAME questions phrased in English scored 7/7 — the vault is
written in English by the fact extractor while the user speaks German, and a
keyword index cannot cross that on its own ("Flugzeuge" never matches
"aircraft").

The bridge is a ``search_aliases`` frontmatter list. It needs no schema or
indexer change: the indexer already flattens every frontmatter value into the
indexed ``frontmatter`` column. What it DOES need is for the relevance filter
to see it — an alias hit matches only in the frontmatter and therefore carries
no body snippet, so a filter judging title+snippet alone would throw away
exactly the hits aliases exist to produce.

These tests drive the real indexer, the real search and the real injector
against a throwaway vault.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.brain.wiki_context import WikiContextInjector
from jarvis.memory.wiki.fts_index import ensure_schema, index_vault
from jarvis.memory.wiki.search import VaultSearch

BASE_PROMPT = "You are Personal Jarvis."

# An English page, as the fact extractor writes them today.
GPU_PAGE_PLAIN = """---
type: entity
slug: nvidia-geforce-rtx-5070-ti
---

# NVIDIA GeForce RTX 5070 Ti

## Summary
The user's GPU, used for local inference workloads.
"""

# The same page with the bridge in place.
GPU_PAGE_BRIDGED = """---
type: entity
slug: nvidia-geforce-rtx-5070-ti
search_aliases: [Grafikkarte, Graka, Grafikprozessor, tarjeta grafica]
---

# NVIDIA GeForce RTX 5070 Ti

## Summary
The user's GPU, used for local inference workloads.
"""


def _vault(tmp_path: Path, pages: dict[str, str]) -> tuple[Path, sqlite3.Connection]:
    root = tmp_path / "vault"
    (root / "entities").mkdir(parents=True)
    for name, text in pages.items():
        (root / "entities" / name).write_text(text, encoding="utf-8")
    # check_same_thread=False mirrors what VaultSearch does for its own
    # connections: the injector runs the (synchronous) search in an executor
    # thread, so a thread-bound connection would fail there.
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    ensure_schema(conn)
    index_vault(root, conn)
    return root, conn


def test_without_aliases_a_german_question_finds_nothing(tmp_path: Path) -> None:
    """Pins the defect itself — if this ever starts passing, the vault
    language changed and the bridge may no longer be needed."""
    root, conn = _vault(tmp_path, {"gpu.md": GPU_PAGE_PLAIN})
    hits = VaultSearch(root, conn=conn).search("Grafikkarte", k=5)
    assert hits == []


def test_aliases_bridge_the_question_to_the_page(tmp_path: Path) -> None:
    root, conn = _vault(tmp_path, {"gpu.md": GPU_PAGE_BRIDGED})
    hits = VaultSearch(root, conn=conn).search("Grafikkarte", k=5)
    assert [h.path.stem for h in hits] == ["gpu"]


@pytest.mark.parametrize(
    "query", ["Grafikkarte", "Graka", "Grafikprozessor", "tarjeta grafica"]
)
def test_every_alias_including_other_languages_resolves(
    tmp_path: Path, query: str
) -> None:
    """Aliases are not a German feature — any supported language may be listed."""
    root, conn = _vault(tmp_path, {"gpu.md": GPU_PAGE_BRIDGED})
    assert VaultSearch(root, conn=conn).search(query, k=5)


def test_alias_hit_carries_frontmatter_and_a_preview(tmp_path: Path) -> None:
    """An alias hit has no body snippet; it must still expose WHY it matched
    and something to show."""
    root, conn = _vault(tmp_path, {"gpu.md": GPU_PAGE_BRIDGED})
    hit = VaultSearch(root, conn=conn).search("Grafikkarte", k=1)[0]

    assert hit.snippet == "", "an alias match is a frontmatter match by contract"
    assert "Grafikkarte" in hit.frontmatter
    assert "GPU" in hit.preview or "inference" in hit.preview
    assert not hit.preview.startswith("#"), "preview must skip the H1"


COMPOUND_PAGE = """---
type: project
slug: drugs-in-schools-project
search_aliases: [Drogenprävention, Suchtprävention]  # i18n-allow: German compound under test
---

# Drugs in schools

## Summary
A prevention project.
"""


def test_prefix_matching_reaches_into_compounds(tmp_path: Path) -> None:
    """FTS5 matches whole tokens, which strands every German compound.

    Measured on the real vault: a question using the first half of a German
    compound did not find the page whose alias was the whole compound. Prefix
    variants close that, and they matter for plurals too.
    """
    root, conn = _vault(tmp_path, {"drugs.md": COMPOUND_PAGE})
    hits = VaultSearch(root, conn=conn).search("Drogen", k=5)
    assert [h.path.stem for h in hits] == ["drugs"]


def test_prefix_matching_does_not_fire_on_tiny_tokens(tmp_path: Path) -> None:
    """A two- or three-letter prefix would match half the vault for nothing."""
    from jarvis.memory.wiki.search import _build_match_expr

    assert "*" not in _build_match_expr(["ab"])
    assert _build_match_expr(["drogen"]) == '"drogen" OR "drogen"*'


def test_exact_token_is_still_searched_alongside_the_prefix(tmp_path: Path) -> None:
    """The prefix is OR-ed IN ADDITION, so exact matches keep their rank."""
    from jarvis.memory.wiki.search import _build_match_expr

    expr = _build_match_expr(["dinner", "viktoria"])
    assert '"dinner"' in expr and '"dinner"*' in expr
    assert '"viktoria"' in expr and '"viktoria"*' in expr


@pytest.mark.asyncio
async def test_relevance_gate_does_not_discard_alias_hits(tmp_path: Path) -> None:
    """The regression this file exists for.

    The relevance filter scores coverage of the question's terms. An alias hit
    matches in the frontmatter only, so judging title+snippet alone would drop
    it — the language bridge and the relevance gate would cancel out.
    """
    root, conn = _vault(tmp_path, {"gpu.md": GPU_PAGE_BRIDGED})
    injector = WikiContextInjector(
        search=VaultSearch(root, conn=conn), latency_budget_ms=2000
    )

    result = await injector.maybe_inject(
        user_text="Welche Grafikkarte habe ich?",  # i18n-allow: German input under test
        system_prompt=BASE_PROMPT,
    )

    assert result != BASE_PROMPT, "the alias hit must survive the relevance gate"
    assert "RTX 5070 Ti" in result
    assert "**NVIDIA GeForce RTX 5070 Ti**: " in result
    # ...and the entry must carry content, not a bare title with nothing after it.
    entry_line = next(
        line for line in result.splitlines() if line.startswith("**NVIDIA")
    )
    assert len(entry_line.split(": ", 1)[1].strip()) > 10


@pytest.mark.asyncio
async def test_the_bridge_does_not_reopen_the_irrelevance_hole(tmp_path: Path) -> None:
    """Aliases must not become a back door for unrelated personal facts."""
    car_page = """---
type: entity
slug: bugatti-divo
search_aliases: [Auto, Sportwagen, Wagen]
---

# Bugatti Divo

## Summary
The user owns six of them.
"""
    root, conn = _vault(tmp_path, {"car.md": car_page})
    injector = WikiContextInjector(
        search=VaultSearch(root, conn=conn), latency_budget_ms=2000
    )

    result = await injector.maybe_inject(
        user_text="Was ist das schnellste Auto der Welt?",  # i18n-allow: German input under test
        system_prompt=BASE_PROMPT,
    )

    assert result == BASE_PROMPT, "general knowledge still never consults memory"
