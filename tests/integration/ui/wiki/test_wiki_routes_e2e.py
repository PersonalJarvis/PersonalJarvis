"""End-to-end test for the Phase B3 Wiki view REST API.

Mounts the full ``WebServer`` (FastAPI app), points
``cfg.wiki_integration.vault_root`` at a temporary directory populated
with three real markdown pages, and walks the same flow the Wave-2
walk-through covers in ``docs/plans/b3/00-OVERVIEW.md §7``:

1. ``GET /api/wiki/tree``                 → 3 files across 2 folders.
2. ``GET /api/wiki/page/harald``          → body contains "1976".
3. ``GET /api/wiki/graph``                → at least 2 edges.
4. ``GET /api/wiki/search?q=pizza``       → 1 hit on the maintainer.
5. ``GET /api/wiki/backlinks/harald``     → 1 hit (the maintainer).

No mocks; the only side-effects are temp-directory file writes and an
HTTP round-trip via FastAPI's ``TestClient``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


def _seed_vault(vault: Path) -> None:
    """Create the three-page demo vault used by the §7 walk-through."""
    (vault / "entities").mkdir(parents=True)
    (vault / "projects").mkdir(parents=True)
    (vault / "concepts").mkdir(parents=True)
    (vault / "sessions").mkdir(parents=True)

    (vault / "entities" / "harald.md").write_text(
        "---\ntype: entity\nslug: harald\n---\n\n"
        "# Harald\n\n## Summary\nHarald is a person born in 1976.\n\n"
        "## Facts\n- Born in 1976.\n",
        encoding="utf-8",
    )
    (vault / "entities" / "the maintainer.md").write_text(
        "---\ntype: entity\nslug: the maintainer\n---\n\n"
        "# the maintainer\n\n## Summary\nFather is [[harald]].\n\n"
        "## Facts\n- Working on [[pixel-art-editor]].\n"
        "- Favorite food is Pizza (source: voice-fact:demo).\n",
        encoding="utf-8",
    )
    (vault / "projects" / "pixel-art-editor.md").write_text(
        "---\ntype: project\nslug: pixel-art-editor\nstatus: active\n---\n\n"
        "# Pixel Art Editor\n\n## Goal\nTiny pixel-art editor in Rust.\n",
        encoding="utf-8",
    )


@pytest.fixture
def server_with_vault(tmp_path: Path) -> WebServer:
    """Build a full WebServer pointing at a freshly seeded temp vault."""
    vault = tmp_path / "vault"
    _seed_vault(vault)

    cfg = JarvisConfig()
    cfg.wiki_integration.vault_root = vault
    # Disable the rollup-worker bootstrap path — this test only exercises
    # the read-only routes, not the curator.
    cfg.wiki_integration.enabled = False

    bus = EventBus()
    return WebServer(cfg=cfg, bus=bus)


def test_full_flow_tree_page_graph_search_backlinks(
    server_with_vault: WebServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # NOTE (product gap, see deep-dive report): the /search route never builds
    # an FTS index — it relies on the AtomicWriter having indexed pages on
    # write. With wiki_integration disabled (this fixture) or a vault edited
    # directly in Obsidian / freshly restored, the index is empty and search
    # returns nothing. Until that gap is closed, this e2e test must build the
    # index itself into an isolated temp DB (otherwise it depends on the
    # shared real data/jarvis.db and is non-hermetic).
    import sqlite3

    fts_index = pytest.importorskip("jarvis.memory.wiki.fts_index")
    db = tmp_path / "fts.db"
    monkeypatch.setattr("jarvis.memory.wiki.search._default_db_path", lambda: db)
    conn = sqlite3.connect(str(db))
    try:
        fts_index.index_vault(tmp_path / "vault", conn)
    finally:
        conn.close()

    with TestClient(server_with_vault.app) as client:
        # 1. tree
        tree = client.get("/api/wiki/tree").json()
        assert tree["ok"] is True
        folders_by_name = {f["name"]: f for f in tree["folders"]}
        assert folders_by_name["entities"]["count"] == 2
        assert folders_by_name["projects"]["count"] == 1
        assert tree["stats"]["total_pages"] == 3

        # 2. page
        page = client.get("/api/wiki/page/harald").json()
        assert page["ok"] is True
        assert page["slug"] == "harald"
        assert "1976" in page["body_md"]
        assert page["frontmatter_valid"] is True

        # 3. graph
        graph = client.get("/api/wiki/graph").json()
        assert graph["ok"] is True
        assert len(graph["edges"]) >= 2
        assert graph["broken"] == []

        # 4. search
        search = client.get("/api/wiki/search", params={"q": "pizza"}).json()
        assert search["ok"] is True
        assert len(search["hits"]) >= 1
        assert search["hits"][0]["slug"] == "the maintainer"

        # 5. backlinks
        backlinks = client.get("/api/wiki/backlinks/harald").json()
        assert backlinks["ok"] is True
        assert any(b["slug"] == "the maintainer" for b in backlinks["backlinks"])
