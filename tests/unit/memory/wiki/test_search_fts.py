"""FTS5-backed VaultSearch tests.

These tests require ``jarvis.memory.wiki.fts_index`` (the peer module).
When that module is not yet importable every test is skipped via
``pytest.importorskip`` so the suite stays green during parallel development.

Test matrix
-----------
- BM25 ranking: frontmatter hit ranks above body-only hit for same token.
- Snippet: non-empty for body hits; empty for frontmatter-only hits.
- FTS5 special characters in query do not crash.
- ``score`` stays within [0.0, 1.0].
- ``k`` limit is honoured.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

fts_index = pytest.importorskip(
    "jarvis.memory.wiki.fts_index",
    reason="fts_index peer module not yet available",
)

from jarvis.memory.wiki.search import VaultSearch  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """Minimal vault with a few markdown pages."""
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite connection with the FTS schema applied."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    fts_index.ensure_schema(conn)
    return conn


def _write_page(vault: Path, rel_path: str, content: str) -> Path:
    p = vault / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _make_search(vault: Path, conn: sqlite3.Connection) -> VaultSearch:
    return VaultSearch(vault, conn=conn)


# ---------------------------------------------------------------------------
# BM25 ranking: frontmatter hit scores higher than body-only hit
# ---------------------------------------------------------------------------


def test_frontmatter_hit_ranks_above_body_hit(tmp_vault, db_conn):
    """A token that appears in the frontmatter should rank above body-only.

    FTS5 BM25 weights are (title=3.0, frontmatter=2.0, body=1.0, mtime=0.0).
    A frontmatter hit therefore has a lower (more negative) raw BM25 score
    and thus a *higher* normalised score.
    """
    # Page whose frontmatter contains the token.
    fm_page = _write_page(
        tmp_vault,
        "entities/alpha.md",
        "---\ntitle: alpha\ntags: uniquetoken\n---\n# Alpha\nNothing relevant here.\n",
    )
    # Page whose body contains the token but frontmatter does not.
    body_page = _write_page(
        tmp_vault,
        "entities/beta.md",
        "---\ntitle: beta\n---\n# Beta\nThis body mentions uniquetoken explicitly.\n",
    )
    fts_index.index_vault(tmp_vault, db_conn)

    vs = _make_search(tmp_vault, db_conn)
    hits = vs.search("uniquetoken", k=5)

    assert len(hits) == 2
    fm_hit = next(h for h in hits if h.path == fm_page)
    body_hit = next(h for h in hits if h.path == body_page)
    assert fm_hit.score >= body_hit.score, (
        f"Frontmatter hit score {fm_hit.score} should be >= body hit score {body_hit.score}"
    )


def test_body_match_strength_affects_ranking(tmp_vault, db_conn):
    """A page matching the query many times in its body must outrank a page
    matching it once.

    Regression for the BM25 column-weight off-by-one: the ``wiki_fts`` table
    has an ``UNINDEXED path`` column at index 0, so passing four weights
    ``bm25(wiki_fts, 3.0, 2.0, 1.0, 0.0)`` actually mapped ``path=3.0,
    title=2.0, frontmatter=1.0, body=0.0`` — leaving the body column with
    weight 0.0, i.e. invisible to ranking. With a dead body weight both pages
    tie at raw bm25 0.0 and the page indexed first wins; the term frequency
    in the body is ignored entirely.
    """
    weak = _write_page(
        tmp_vault,
        "concepts/weak.md",
        "---\ntype: concept\nslug: weak\n---\n# Weak page\n"
        "needle padding padding padding padding padding padding padding.\n",
    )
    strong = _write_page(
        tmp_vault,
        "concepts/strong.md",
        "---\ntype: concept\nslug: strong\n---\n# Strong page\n"
        "needle needle needle needle needle needle.\n",
    )
    # Index the WEAK page first so that, under the bug (body weight 0.0 →
    # both raw scores tie at 0.0), the weak page sorts first. With the fix
    # (body weight 1.0) the higher term-frequency strong page wins.
    fts_index.upsert_page(db_conn, tmp_vault, weak)
    fts_index.upsert_page(db_conn, tmp_vault, strong)

    vs = _make_search(tmp_vault, db_conn)
    hits = vs.search("needle", k=5)

    assert [h.path.name for h in hits] == ["strong.md", "weak.md"], (
        "strong body match must rank above weak body match; got "
        f"{[h.path.name for h in hits]}"
    )


# ---------------------------------------------------------------------------
# Snippet presence
# ---------------------------------------------------------------------------


def test_snippet_nonempty_for_body_hit(tmp_vault, db_conn):
    _write_page(
        tmp_vault,
        "concepts/gamma.md",
        "---\ntitle: gamma\n---\n# Gamma\nThe word findme appears here in the body text.\n",
    )
    fts_index.index_vault(tmp_vault, db_conn)

    vs = _make_search(tmp_vault, db_conn)
    hits = vs.search("findme", k=5)

    assert hits, "Expected at least one hit"
    # The body column (index 3 in the virtual table) has the match, so
    # snippet() should produce a non-empty string.
    assert hits[0].snippet != "", "snippet should be non-empty for a body hit"


def test_snippet_empty_for_frontmatter_only_hit(tmp_vault, db_conn):
    """When the token only appears in the frontmatter column, snippet is empty.

    ``snippet(wiki_fts, 3, ...)`` targets column index 3 (body).  If the
    match is only in frontmatter (column 2), SQLite returns an empty snippet.
    """
    _write_page(
        tmp_vault,
        "concepts/delta.md",
        "---\ntitle: delta\nkeyword: onlyinfrontmatter\n---\n# Delta\nNo relevant body.\n",
    )
    fts_index.index_vault(tmp_vault, db_conn)

    vs = _make_search(tmp_vault, db_conn)
    hits = vs.search("onlyinfrontmatter", k=5)

    assert hits, "Expected at least one hit"
    assert hits[0].snippet == "", (
        "snippet should be empty when the match is frontmatter-only"
    )


# ---------------------------------------------------------------------------
# FTS5 special characters in query must not crash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_query", [
    '"foo*bar"',
    "a:b",
    "-x",
    "foo AND bar",
    "((nested))",
    "term^boost",
    '"quoted phrase"',
])
def test_special_chars_in_query_do_not_crash(tmp_vault, db_conn, bad_query):
    _write_page(tmp_vault, "concepts/safe.md", "---\ntitle: safe\n---\n# Safe\nBody.\n")
    fts_index.index_vault(tmp_vault, db_conn)

    vs = _make_search(tmp_vault, db_conn)
    # Must not raise; result may be empty.
    result = vs.search(bad_query, k=5)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# score is in [0.0, 1.0]
# ---------------------------------------------------------------------------


def test_score_in_range(tmp_vault, db_conn):
    _write_page(
        tmp_vault,
        "entities/epsilon.md",
        "---\ntitle: epsilon\n---\n# Epsilon\nThis page contains scorecheck for testing.\n",
    )
    fts_index.index_vault(tmp_vault, db_conn)

    vs = _make_search(tmp_vault, db_conn)
    hits = vs.search("scorecheck", k=5)

    assert hits
    for h in hits:
        assert 0.0 <= h.score <= 1.0, f"score {h.score} out of [0,1]"


# ---------------------------------------------------------------------------
# k limit is honoured
# ---------------------------------------------------------------------------


def test_k_limit_honoured(tmp_vault, db_conn):
    for i in range(10):
        _write_page(
            tmp_vault,
            f"entities/page{i}.md",
            f"---\ntitle: page{i}\n---\n# Page {i}\nCommontoken appears here.\n",
        )
    fts_index.index_vault(tmp_vault, db_conn)

    vs = _make_search(tmp_vault, db_conn)
    hits = vs.search("commontoken", k=3)

    assert len(hits) <= 3


# ---------------------------------------------------------------------------
# Empty query
# ---------------------------------------------------------------------------


def test_empty_query_returns_empty(tmp_vault, db_conn):
    _write_page(tmp_vault, "entities/zeta.md", "---\ntitle: zeta\n---\n# Zeta\nBody.\n")
    fts_index.index_vault(tmp_vault, db_conn)

    vs = _make_search(tmp_vault, db_conn)
    assert vs.search("") == []
    assert vs.search("   ") == []
