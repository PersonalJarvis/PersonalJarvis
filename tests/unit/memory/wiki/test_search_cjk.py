"""Japanese/Chinese pages are findable: CJK text has no spaces between words.

The ``unicode61`` tokenizer made a whole Japanese sentence ONE token, so a
question never matched the page that answers it. The index now carries a CJK
unigram/bigram column and a CJK query becomes content kanji + katakana bigrams.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from jarvis.brain.wiki_relevance import relevant_hits, should_consult_memory
from jarvis.memory.wiki import cjk, fts_index
from jarvis.memory.wiki.search import VaultSearch

# "The user's favourite colour is jade green."
_PAGE = "# 好み\n\nユーザーが好きな色は翡翠色である。\n"
# "What was my favourite colour?"
_QUESTION = "私の好きな色は何だった？"


def _index(tmp_path: Path, pages: dict[str, str]) -> VaultSearch:
    vault = tmp_path / "vault"
    for rel, text in pages.items():
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    fts_index.index_vault(vault, conn)
    return VaultSearch(vault, conn=conn)


def test_japanese_question_finds_the_japanese_page(tmp_path: Path) -> None:
    search = _index(tmp_path, {"prefs.md": _PAGE, "other.md": "# Other\n\nCoffee notes.\n"})
    hits = search.search(_QUESTION, k=5)
    assert [h.path.name for h in hits][:1] == ["prefs.md"]


def test_the_relevance_gate_consults_and_keeps_the_hit(tmp_path: Path) -> None:
    verdict = should_consult_memory(_QUESTION)
    assert verdict.consult
    search = _index(tmp_path, {"prefs.md": _PAGE})
    kept = relevant_hits(search.search(_QUESTION, k=5), _QUESTION, min_coverage=0.75)
    assert len(kept) == 1


def test_question_terms_drop_grammar_and_question_words() -> None:
    # like, colour — not the particles, not "I"/"what".
    assert cjk.query_terms(_QUESTION) == ["好", "色"]


def test_a_pre_cjk_index_is_dropped_so_it_can_be_rebuilt() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE VIRTUAL TABLE wiki_fts USING fts5(path UNINDEXED, title, frontmatter, "
        "body, mtime UNINDEXED)"
    )
    fts_index.ensure_schema(conn)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(wiki_fts)").fetchall()]
    assert "cjk" in cols


def test_latin_search_is_unchanged(tmp_path: Path) -> None:
    search = _index(tmp_path, {"coffee.md": "# Coffee\n\nRuben likes espresso.\n"})
    assert [h.path.name for h in search.search("espresso", k=5)] == ["coffee.md"]


def test_a_japanese_personal_question_uses_the_standard_bar() -> None:
    verdict = should_consult_memory(_QUESTION)
    assert verdict.consult and not verdict.strict
