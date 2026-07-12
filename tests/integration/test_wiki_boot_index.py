"""Proof that a pre-existing vault returns search hits after boot indexing.

Regression guard: ``wiki_fts`` used to be populated only incrementally by
``AtomicWriter`` writes, so a fresh clone / restored vault returned zero
search hits until a page was rewritten. ``WebServer._init_wiki_boot_index``
builds the FTS index once at boot when the table is empty; this test proves
a populated vault is searchable straight after that hook with no manual
``reindex``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from jarvis.memory.wiki.search import VaultSearch
from jarvis.ui.web.server import WebServer


def _write_page(vault: Path, rel_path: str, content: str) -> None:
    p = vault / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _fake_cfg(vault_root: Path, data_dir: Path) -> SimpleNamespace:
    """Minimal stand-in exposing only the two attributes the hook reads."""
    return SimpleNamespace(
        wiki_integration=SimpleNamespace(enabled=True, vault_root=vault_root),
        memory=SimpleNamespace(data_dir=str(data_dir)),
    )


def test_populated_vault_searchable_after_boot_index(tmp_path: Path) -> None:
    vault = tmp_path / "wiki" / "obsidian-vault"
    vault.mkdir(parents=True)
    _write_page(
        vault,
        "entities/ruben.md",
        "---\naliases: [Ruben, boss]\n---\n# Ruben\n\n"
        "Ruben drives a turquoise sailboat named Albatross.\n",
    )
    _write_page(
        vault,
        "topics/sailing.md",
        "# Sailing\n\nNotes about the Albatross sailboat and harbour logistics.\n",
    )

    data_dir = tmp_path / "data"
    db_path = data_dir / "jarvis.db"

    # Sanity: before the boot index the FTS table does not exist yet, so a
    # search yields nothing.
    search_before = VaultSearch(vault, db_path=db_path)
    assert search_before.search("Albatross") == []
    search_before.close()

    # Drive only the boot-index hook with a fake config object.
    server = WebServer.__new__(WebServer)
    server.cfg = _fake_cfg(vault, data_dir)
    server._init_wiki_boot_index()

    # The shared DB now has the FTS rows.
    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM wiki_fts").fetchone()[0] == 2
    finally:
        conn.close()

    # A fresh VaultSearch over the SAME db file returns a hit — no manual
    # reindex was run.
    search = VaultSearch(vault, db_path=db_path)
    try:
        hits = search.search("Albatross")
        assert hits, "expected at least one hit after boot index"
        titles = {h.title for h in hits}
        assert "Ruben" in titles or "Sailing" in titles
    finally:
        search.close()


def test_boot_index_reconciles_stale_populated_index(tmp_path: Path) -> None:
    """A non-empty index is rebuilt when the active vault contents change."""
    vault = tmp_path / "wiki" / "obsidian-vault"
    vault.mkdir(parents=True)
    _write_page(vault, "topics/sailing.md", "# Sailing\n\nThe Albatross sailboat.\n")

    data_dir = tmp_path / "data"
    db_path = data_dir / "jarvis.db"

    server = WebServer.__new__(WebServer)
    server.cfg = _fake_cfg(vault, data_dir)
    server._init_wiki_boot_index()
    (vault / "topics" / "sailing.md").unlink()
    _write_page(vault, "topics/aviation.md", "# Aviation\n\nElectric aircraft.\n")
    server._init_wiki_boot_index()

    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM wiki_fts").fetchone()[0] == 1
        assert conn.execute("SELECT path FROM wiki_fts").fetchone()[0] == (
            "topics/aviation.md"
        )
    finally:
        conn.close()


def test_boot_index_missing_vault_is_noop(tmp_path: Path) -> None:
    """A missing vault directory must not raise and must not create rows."""
    data_dir = tmp_path / "data"
    server = WebServer.__new__(WebServer)
    server.cfg = _fake_cfg(tmp_path / "wiki" / "obsidian-vault", data_dir)
    server._init_wiki_boot_index()  # vault dir does not exist
    # The hook returns before opening the DB, so no file is created.
    assert not (data_dir / "jarvis.db").exists()
