import sqlite3
from pathlib import Path

from jarvis.memory.wiki.semantic_index import ensure_schema, upsert_page
from jarvis.memory.wiki.semantic_search import SemanticVaultSearch


class FakeEmbedder:
    def embed(self, texts):
        for text in texts:
            t = text.lower()
            if "testing integrations" in t or "verify linked platform" in t:
                yield [1.0, 0.0]
            else:
                yield [0.0, 1.0]


def test_semantic_search_finds_paraphrased_note(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "concepts").mkdir(parents=True)

    page = vault / "concepts" / "obsidian-test.md"
    page.write_text(
        "---\ntype: concept\n---\n"
        "Martí prefers testing integrations with one concrete step at a time.\n",
        encoding="utf-8",
    )

    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    upsert_page(conn, vault, page, embedder=FakeEmbedder())

    search = SemanticVaultSearch(vault, embedder=FakeEmbedder(), conn=conn)
    hits = search.search("procedure verify linked platform")

    assert hits
    assert hits[0].path.name == "obsidian-test.md"
    assert "testing integrations" in hits[0].preview


def test_empty_query_returns_no_hits(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    search = SemanticVaultSearch(tmp_path, embedder=FakeEmbedder(), conn=conn)
    assert search.search("   ") == []


def test_warm_up_initializes_embedder(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    search = SemanticVaultSearch(tmp_path, embedder=FakeEmbedder(), conn=conn)
    search.warm_up()
