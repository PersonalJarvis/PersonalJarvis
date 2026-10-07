import sqlite3
from pathlib import Path

from jarvis.memory.wiki.semantic_index import (
    ensure_schema,
    remove_page,
    upsert_page,
)


class FakeEmbedder:
    def embed(self, texts):
        for text in texts:
            yield [float(len(text)), 1.0]


def test_semantic_index_upsert_and_remove(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "concepts").mkdir(parents=True)

    page = vault / "concepts" / "note.md"
    page.write_text(
        "---\ntype: concept\n---\n"
        "First useful paragraph with enough content.\n\n"
        "Second useful paragraph with enough content too.\n",
        encoding="utf-8",
    )

    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)

    upsert_page(conn, vault, page, embedder=FakeEmbedder())

    rows = conn.execute(
        "SELECT path, chunk_index, content FROM wiki_semantic ORDER BY chunk_index"
    ).fetchall()

    assert len(rows) == 2
    assert rows[0][0] == "concepts/note.md"

    remove_page(conn, vault, page)
    assert conn.execute("SELECT COUNT(*) FROM wiki_semantic").fetchone()[0] == 0


def test_semantic_index_rebuild_indexes_existing_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "concepts").mkdir(parents=True)

    (vault / "concepts" / "a.md").write_text(
        "Useful paragraph with enough content to index semantically.",
        encoding="utf-8",
    )
    (vault / "concepts" / "b.md").write_text(
        "Another useful paragraph with enough content to index.",
        encoding="utf-8",
    )

    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)

    from jarvis.memory.wiki.semantic_index import rebuild_index

    count = rebuild_index(conn=conn, vault_root=vault, embedder=FakeEmbedder())

    assert count == 2
    assert conn.execute("SELECT COUNT(*) FROM wiki_semantic").fetchone()[0] == 2
