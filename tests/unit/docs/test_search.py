"""Unit tests for DocSearch (SQLite-FTS5)."""
from __future__ import annotations

from pathlib import Path

import pytest

import jarvis.docs.search as search_module
from jarvis.docs.schema import Doc, DocFrontmatter
from jarvis.docs.search import DocSearch


def _doc(
    slug: str,
    title: str,
    body: str,
    diataxis: str = "explanation",
    phase: str = "5",
    tags: list[str] | None = None,
) -> Doc:
    fm = DocFrontmatter(
        title=title,
        slug=slug,
        diataxis=diataxis,
        phase=phase,
        tags=tags or [],
    )
    return Doc(path=Path("docs") / f"{slug}.md", frontmatter=fm, body=body)


@pytest.fixture
def search(tmp_path: Path) -> DocSearch:
    s = DocSearch(tmp_path / "test_index.sqlite")
    yield s
    s.close()


# ----------------------------------------------------------------------
# Upsert / Delete
# ----------------------------------------------------------------------

def test_upsert_and_query_basic(search: DocSearch) -> None:
    search.upsert(_doc(
        slug="router-discipline",
        title="Concept: Router-Discipline",
        body="The supervisor is a pure dispatcher and delegates via spawn_worker.",
    ))
    results = search.query("Dispatcher")
    assert len(results) == 1
    assert results[0].slug == "router-discipline"
    assert "Dispatcher" in results[0].snippet or "<mark>" in results[0].snippet


def test_upsert_replaces_existing(search: DocSearch) -> None:
    search.upsert(_doc("foo", "Foo v1", "Original body about hammer."))
    search.upsert(_doc("foo", "Foo v2", "Replaced body about screwdriver."))
    assert search.query("hammer") == []
    results = search.query("screwdriver")
    assert len(results) == 1
    assert results[0].title == "Foo v2"


def test_delete_removes_from_index(search: DocSearch) -> None:
    search.upsert(_doc("temp", "Temporary", "Disappear soon."))
    assert len(search.query("Disappear")) == 1
    search.delete("temp")
    assert search.query("Disappear") == []


def test_replace_all_atomic(search: DocSearch) -> None:
    search.upsert(_doc("old", "Old", "Will be wiped."))
    search.replace_all([
        _doc("a", "A", "Alpha content"),
        _doc("b", "B", "Beta content"),
    ])
    assert search.query("wiped") == []
    assert len(search.query("Alpha")) == 1
    assert len(search.query("Beta")) == 1


def test_replace_all_removes_retired_content_from_raw_database_files(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "privacy_index.sqlite"
    retired_marker = b"RETIRED_PRIVATE_DOC_SENTINEL_7f6c05a9"
    search = DocSearch(db_path)
    try:
        search.upsert(
            _doc("retired", "Retired", retired_marker.decode("ascii")),
        )
        database_files = [
            db_path,
            Path(f"{db_path}-wal"),
            Path(f"{db_path}-shm"),
        ]
        assert any(
            retired_marker in path.read_bytes()
            for path in database_files
            if path.exists()
        )

        search.replace_all([_doc("public", "Public", "Public reader content")])

        assert search.query("RETIRED_PRIVATE_DOC_SENTINEL_7f6c05a9") == []
        assert len(search.query("Public")) == 1
        assert all(
            retired_marker not in path.read_bytes()
            for path in database_files
            if path.exists()
        )
    finally:
        search.close()


def test_replace_all_keeps_previous_index_when_atomic_swap_fails(
    search: DocSearch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search.upsert(_doc("stable", "Stable", "Previous searchable content"))

    def fail_replace(*_args: object) -> None:
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(search_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        search.replace_all([_doc("new", "New", "Replacement content")])

    assert len(search.query("Previous")) == 1
    assert search.query("Replacement") == []


# ----------------------------------------------------------------------
# Query — Filter + BM25
# ----------------------------------------------------------------------

def test_filter_by_diataxis(search: DocSearch) -> None:
    search.upsert(_doc("c1", "C1", "voice pipeline", diataxis="explanation"))
    search.upsert(_doc("h1", "H1", "voice pipeline", diataxis="howto"))
    results = search.query("voice", diataxis="howto")
    assert len(results) == 1
    assert results[0].slug == "h1"


def test_query_orders_by_rank(search: DocSearch) -> None:
    """BM25 ranking: a doc that has the term multiple times + in the title
    should rank above a doc with only one hit in the body."""
    search.upsert(_doc(
        "primary", "Routing Discipline",
        body="routing routing routing routing logic for the router brain",
    ))
    search.upsert(_doc(
        "secondary", "Other",
        body="this mentions routing only once",
    ))
    results = search.query("routing")
    assert len(results) == 2
    assert results[0].slug == "primary"


def test_query_with_phrase(search: DocSearch) -> None:
    search.upsert(_doc("a", "A", "the quick brown fox"))
    search.upsert(_doc("b", "B", "the brown quick fox"))
    # The phrase match occurs only in A.
    results = search.query('"quick brown"')
    slugs = [r.slug for r in results]
    assert "a" in slugs


# ----------------------------------------------------------------------
# Query — Sanitization
# ----------------------------------------------------------------------

def test_query_empty_returns_empty(search: DocSearch) -> None:
    search.upsert(_doc("a", "A", "content"))
    assert search.query("") == []
    assert search.query("   ") == []


def test_query_handles_unbalanced_quote(search: DocSearch) -> None:
    """An open quote should not crash — the sanitizer removes it."""
    search.upsert(_doc("a", "A", "hello world"))
    results = search.query('"hello')  # unbalanced quote
    # Either empty or with a hit — the main thing is no OperationalError
    assert isinstance(results, list)


def test_query_handles_only_operators(search: DocSearch) -> None:
    search.upsert(_doc("a", "A", "content"))
    # The sanitizer removes input made only from operators.
    results = search.query("+++")
    assert results == []


# ----------------------------------------------------------------------
# Snippet & Highlight
# ----------------------------------------------------------------------

def test_snippet_contains_mark_tags(search: DocSearch) -> None:
    search.upsert(_doc(
        "doc",
        "Doc",
        body=(
            "Lorem ipsum dolor sit amet. "
            "The Voice-Pipeline is Phase 1 of Personal-Jarvis. "
            "Sed do eiusmod tempor incididunt."
        ),
    ))
    results = search.query("Voice-Pipeline")
    assert len(results) == 1
    snippet = results[0].snippet
    assert "<mark>" in snippet
    assert "</mark>" in snippet
