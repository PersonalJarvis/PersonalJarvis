"""Unit tests for jarvis.memory.wiki.fts_index.

All tests use an in-memory SQLite connection so they are fast and fully
isolated from disk state.

Coverage
--------
- ``ensure_schema`` is idempotent (called twice, no error, table exists).
- ``index_vault`` on a tmp vault with 3 ``.md`` files produces 3 rows.
  Files: one with frontmatter, one without, one with a list-valued alias.
- ``upsert_page`` replaces (not duplicates) a row when called twice on the
  same path.
- ``remove_page`` deletes the row keyed by path.
- A basic ``MATCH`` query against ``wiki_fts`` returns the expected row.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.memory.wiki.fts_index import (
    ensure_schema,
    index_vault,
    remove_page,
    upsert_page,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mem_conn() -> sqlite3.Connection:
    """Return a fresh in-memory connection with wiki_fts schema applied."""
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    return conn


def _row_count(conn: sqlite3.Connection) -> int:
    """Return the number of rows in wiki_fts."""
    return conn.execute("SELECT COUNT(*) FROM wiki_fts").fetchone()[0]


def _write_md(path: Path, content: str) -> Path:
    """Write *content* to *path* and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Test: ensure_schema idempotency
# ---------------------------------------------------------------------------


def test_ensure_schema_idempotent() -> None:
    """Calling ensure_schema twice on the same connection must not raise."""
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    ensure_schema(conn)  # second call — must be a no-op
    # Verify the table exists by querying it.
    conn.execute("SELECT * FROM wiki_fts LIMIT 0")
    conn.close()


# ---------------------------------------------------------------------------
# Test: index_vault produces correct row count
# ---------------------------------------------------------------------------


def test_index_vault_three_pages(tmp_path: Path) -> None:
    """index_vault on a vault with 3 .md files must produce exactly 3 rows."""
    entities = tmp_path / "entities"
    entities.mkdir()

    # Page 1: has frontmatter with simple scalar values.
    _write_md(
        entities / "ruben.md",
        "---\ntype: entity\nslug: ruben\n---\n# Ruben\nThis is the body.\n",
    )

    # Page 2: no frontmatter at all.
    _write_md(
        tmp_path / "readme.md",
        "# ReadMe\nJust a body paragraph.\n",
    )

    # Page 3: frontmatter with a list-valued aliases field.
    _write_md(
        entities / "jarvis.md",
        "---\ntype: entity\nslug: jarvis\naliases: [J, Personal Jarvis, PJ]\n---\n# Jarvis\nAssistant.\n",
    )

    conn = _mem_conn()
    count = index_vault(tmp_path, conn)

    assert count == 3
    assert _row_count(conn) == 3
    conn.close()


def test_index_vault_returns_zero_for_empty_vault(tmp_path: Path) -> None:
    """An empty vault directory produces 0 rows."""
    conn = _mem_conn()
    count = index_vault(tmp_path, conn)
    assert count == 0
    assert _row_count(conn) == 0
    conn.close()


def test_index_vault_idempotent(tmp_path: Path) -> None:
    """Running index_vault twice must not duplicate rows."""
    _write_md(tmp_path / "note.md", "# Note\nBody.\n")

    conn = _mem_conn()
    index_vault(tmp_path, conn)
    count2 = index_vault(tmp_path, conn)

    assert count2 == 1
    assert _row_count(conn) == 1
    conn.close()


# ---------------------------------------------------------------------------
# Test: upsert_page replaces, does not duplicate
# ---------------------------------------------------------------------------


def test_upsert_page_no_duplicate(tmp_path: Path) -> None:
    """upsert_page called twice on the same path must leave exactly 1 row."""
    page = _write_md(tmp_path / "thing.md", "# Thing\nOriginal body.\n")

    conn = _mem_conn()
    upsert_page(conn, tmp_path, page)
    upsert_page(conn, tmp_path, page)  # second upsert — same path

    assert _row_count(conn) == 1
    conn.close()


def test_upsert_page_updates_content(tmp_path: Path) -> None:
    """After updating the file and calling upsert_page, the new body is indexed."""
    page = _write_md(tmp_path / "evolving.md", "# EV\nFirst version.\n")

    conn = _mem_conn()
    upsert_page(conn, tmp_path, page)

    # Overwrite with new content.
    page.write_text("# EV\nSecond version — completely different.\n", encoding="utf-8")
    upsert_page(conn, tmp_path, page)

    assert _row_count(conn) == 1
    body = conn.execute("SELECT body FROM wiki_fts WHERE path = ?", ("evolving.md",)).fetchone()[0]
    assert "Second version" in body
    assert "First version" not in body
    conn.close()


# ---------------------------------------------------------------------------
# Test: remove_page deletes by path
# ---------------------------------------------------------------------------


def test_remove_page_deletes_row(tmp_path: Path) -> None:
    """remove_page must reduce the row count by 1."""
    page = _write_md(tmp_path / "gone.md", "# Gone\nBody.\n")

    conn = _mem_conn()
    upsert_page(conn, tmp_path, page)
    assert _row_count(conn) == 1

    remove_page(conn, tmp_path, page)
    assert _row_count(conn) == 0
    conn.close()


def test_remove_page_noop_when_absent(tmp_path: Path) -> None:
    """Removing a path that was never inserted must not raise."""
    page = tmp_path / "nonexistent.md"

    conn = _mem_conn()
    remove_page(conn, tmp_path, page)  # must not raise
    conn.close()


# ---------------------------------------------------------------------------
# Test: FTS5 MATCH query returns expected row
# ---------------------------------------------------------------------------


def test_fts_match_returns_correct_hit(tmp_path: Path) -> None:
    """A MATCH query must return the row whose body contains the search term."""
    _write_md(tmp_path / "alpha.md", "# Alpha\nThis page is about foxes.\n")
    _write_md(tmp_path / "beta.md", "# Beta\nThis page is about wolves.\n")

    conn = _mem_conn()
    index_vault(tmp_path, conn)

    rows = conn.execute(
        "SELECT path, title FROM wiki_fts WHERE wiki_fts MATCH ? ORDER BY rank",
        ('"foxes"',),
    ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "alpha.md"
    assert rows[0][1] == "Alpha"
    conn.close()


def test_fts_match_frontmatter_aliases_searchable(tmp_path: Path) -> None:
    """List-valued aliases in frontmatter must be searchable via FTS MATCH."""
    _write_md(
        tmp_path / "jarvis.md",
        "---\ntype: entity\nslug: jarvis\naliases: [PJ, PersonalJarvis]\n---\n# Jarvis\nBody.\n",
    )

    conn = _mem_conn()
    index_vault(tmp_path, conn)

    rows = conn.execute(
        "SELECT path FROM wiki_fts WHERE wiki_fts MATCH ?",
        ('"PJ"',),
    ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "jarvis.md"
    conn.close()


def test_fts_match_no_hit_for_absent_term(tmp_path: Path) -> None:
    """A MATCH query for a term not present in the vault must return 0 rows."""
    _write_md(tmp_path / "page.md", "# Page\nThis talks about cats.\n")

    conn = _mem_conn()
    index_vault(tmp_path, conn)

    rows = conn.execute(
        "SELECT path FROM wiki_fts WHERE wiki_fts MATCH ?",
        ('"dinosaurs"',),
    ).fetchall()

    assert rows == []
    conn.close()


# ---------------------------------------------------------------------------
# Test: path stored as vault-relative POSIX string
# ---------------------------------------------------------------------------


def test_path_stored_as_posix_relative(tmp_path: Path) -> None:
    """The path column must be vault-root-relative with forward slashes."""
    sub = tmp_path / "entities"
    sub.mkdir()
    page = _write_md(sub / "alice.md", "# Alice\nBody.\n")

    conn = _mem_conn()
    upsert_page(conn, tmp_path, page)

    stored = conn.execute("SELECT path FROM wiki_fts").fetchone()[0]
    assert stored == "entities/alice.md"
    assert "\\" not in stored
    conn.close()
