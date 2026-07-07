"""Unit tests for the Phase B3 Wiki-view REST routes.

Mounts ``wiki_routes.router`` on a fresh FastAPI app, points the
``app.state.config`` at a temporary vault directory, and asserts the
JSON shapes defined in ``docs/plans/b3/00-OVERVIEW.md §3.1``.

The tests use real files in ``tmp_path`` (AP-5 forbids mocking the
filesystem). They never write to the real ``wiki/obsidian-vault/``.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.wiki_routes import router as wiki_router


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _make_app(vault_root: Path | None) -> FastAPI:
    """Build a minimal FastAPI app with the wiki router mounted.

    ``vault_root=None`` simulates a wiki-integration-disabled config.
    """
    app = FastAPI()
    app.include_router(wiki_router)
    if vault_root is None:
        wiki_cfg = SimpleNamespace(vault_root=None)
    else:
        wiki_cfg = SimpleNamespace(vault_root=vault_root)
    app.state.config = SimpleNamespace(wiki_integration=wiki_cfg)
    return app


def _write_page(
    vault_root: Path,
    subdir: str,
    slug: str,
    *,
    page_type: str,
    body: str,
    extra_fm: dict[str, str] | None = None,
) -> Path:
    """Helper: write a schema-valid markdown page into the vault."""
    folder = vault_root / subdir
    folder.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        "---",
        f"type: {page_type}",
        f"slug: {slug}",
    ]
    if page_type == "project":
        fm_lines.append("status: active")
    for key, value in (extra_fm or {}).items():
        fm_lines.append(f"{key}: {value}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(body)
    path = folder / f"{slug}.md"
    path.write_text("\n".join(fm_lines), encoding="utf-8")
    return path


@pytest.fixture
def populated_vault(tmp_path: Path) -> Path:
    """Three-page vault: ruben -> harald, ruben -> pixel-art-editor."""
    vault = tmp_path / "vault"
    _write_page(
        vault,
        "entities",
        "harald",
        page_type="entity",
        body="# Harald\n\n## Summary\nHarald is a person born in 1976.\n\n## Facts\n- Born in 1976.\n",
    )
    _write_page(
        vault,
        "entities",
        "ruben",
        page_type="entity",
        body=(
            "# Ruben\n\n## Summary\nFather is [[harald]].\n\n"
            "## Facts\n- Working on [[pixel-art-editor]].\n"
            "- Favorite food is Pizza (source: voice-fact:demo).\n"
        ),
    )
    _write_page(
        vault,
        "projects",
        "pixel-art-editor",
        page_type="project",
        body="# Pixel Art Editor\n\n## Goal\nTiny pixel-art editor in Rust.\n",
    )
    return vault


@pytest.fixture
def empty_vault(tmp_path: Path) -> Path:
    """Vault directory with the four standard subfolders, but no pages."""
    vault = tmp_path / "vault"
    for sub in ("entities", "concepts", "projects", "sessions"):
        (vault / sub).mkdir(parents=True, exist_ok=True)
    return vault


# ----------------------------------------------------------------------
# /tree
# ----------------------------------------------------------------------


def test_tree_with_three_pages_lists_files_and_counts(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/tree")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    folders_by_name = {f["name"]: f for f in body["folders"]}
    assert folders_by_name["entities"]["count"] == 2
    assert folders_by_name["projects"]["count"] == 1
    assert folders_by_name["concepts"]["count"] == 0
    assert folders_by_name["sessions"]["count"] == 0
    slugs = {f["slug"] for f in folders_by_name["entities"]["files"]}
    assert slugs == {"harald", "ruben"}
    sample_file = folders_by_name["entities"]["files"][0]
    assert "mtime" in sample_file and isinstance(sample_file["mtime"], float)
    assert "size" in sample_file and sample_file["size"] > 0
    assert body["stats"]["total_pages"] == 3
    # ruben has 2 outbound wikilinks (harald, pixel-art-editor)
    assert body["stats"]["total_links"] >= 2


def test_tree_with_empty_vault_returns_four_empty_buckets(empty_vault: Path) -> None:
    app = _make_app(empty_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/tree")
    body = r.json()
    assert body["ok"] is True
    assert len(body["folders"]) == 4
    for folder in body["folders"]:
        assert folder["count"] == 0
        assert folder["files"] == []
    assert body["stats"]["total_pages"] == 0


def test_tree_with_missing_vault_returns_empty_ok_response(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    app = _make_app(missing)
    with TestClient(app) as client:
        r = client.get("/api/wiki/tree")
    body = r.json()
    assert body["ok"] is True
    assert body["stats"]["total_pages"] == 0
    assert all(folder["files"] == [] for folder in body["folders"])


# ----------------------------------------------------------------------
# /page/{slug}
# ----------------------------------------------------------------------


def test_page_happy_path_returns_frontmatter_body_wikilinks(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/page/ruben")
    body = r.json()
    assert body["ok"] is True
    assert body["slug"] == "ruben"
    assert body["kind"] == "entity"
    assert body["frontmatter_valid"] is True
    assert body["frontmatter"]["type"] == "entity"
    assert "Father is [[harald]]" in body["body_md"]
    assert set(body["wikilinks"]) == {"harald", "pixel-art-editor"}
    assert body["stats"]["bytes"] > 0
    assert body["stats"]["words"] > 0
    assert body["path"].endswith("entities/ruben.md")


def test_page_unknown_slug_returns_not_found_envelope(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/page/does-not-exist")
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is False
    assert "not found" in body["error"]


def test_page_rejects_path_traversal_slug(populated_vault: Path) -> None:
    """A slug that could escape the vault must be rejected before any disk
    probe. On Windows a backslash is a valid single URL path segment, so
    ``..\\..\\x`` reaches the handler and ``vault_root / dir / f"{slug}.md"``
    would resolve outside the vault. The guard must reject it.
    """
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        for bad in ("..\\..\\secret", "..\\..\\..\\Windows\\win", "foo\\bar", "a:b"):
            r = client.get(f"/api/wiki/page/{bad}")
            body = r.json()
            assert body["ok"] is False, f"{bad!r} should be rejected"
            assert "invalid" in body["error"], f"{bad!r} gave {body}"


def test_page_schema_invalid_still_returns_page_with_flag(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    folder = vault / "entities"
    folder.mkdir(parents=True)
    # Missing 'slug' frontmatter key — schema validation fails, but the
    # endpoint must still return the page so the UI can warn.
    (folder / "broken.md").write_text(
        "---\ntype: entity\n---\n\n# Broken\n\nBody text.\n",
        encoding="utf-8",
    )
    app = _make_app(vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/page/broken")
    body = r.json()
    assert body["ok"] is True
    assert body["slug"] == "broken"
    assert body["frontmatter_valid"] is False
    assert "Body text" in body["body_md"]


# ----------------------------------------------------------------------
# /graph
# ----------------------------------------------------------------------


def test_graph_with_linked_pages_produces_nodes_and_edges(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/graph")
    body = r.json()
    assert body["ok"] is True
    node_ids = {n["id"] for n in body["nodes"]}
    assert node_ids == {"harald", "ruben", "pixel-art-editor"}
    edge_pairs = {(e["source"], e["target"]) for e in body["edges"]}
    assert ("ruben", "harald") in edge_pairs
    assert ("ruben", "pixel-art-editor") in edge_pairs
    assert body["broken"] == []
    # Edge contexts include the wikilink in question.
    for edge in body["edges"]:
        assert edge["context"] != ""


def test_graph_with_broken_wikilink_lists_it_in_broken_bucket(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_page(
        vault,
        "entities",
        "alice",
        page_type="entity",
        body="# Alice\n\n## Summary\nAlice knows [[ghost-page]].\n",
    )
    app = _make_app(vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/graph")
    body = r.json()
    assert body["ok"] is True
    assert body["edges"] == []
    assert len(body["broken"]) == 1
    assert body["broken"][0]["source"] == "alice"
    assert body["broken"][0]["target"] == "ghost-page"


# ----------------------------------------------------------------------
# /backlinks/{slug}
# ----------------------------------------------------------------------


def test_backlinks_for_harald_includes_ruben_with_snippet(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/backlinks/harald")
    body = r.json()
    assert body["ok"] is True
    assert body["slug"] == "harald"
    backlinks_by_slug = {b["slug"]: b for b in body["backlinks"]}
    assert "ruben" in backlinks_by_slug
    snippet = backlinks_by_slug["ruben"]["snippet"]
    assert "harald" in snippet.lower()


def test_backlinks_for_unreferenced_slug_returns_empty_list(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/backlinks/orphan")
    body = r.json()
    assert body["ok"] is True
    assert body["backlinks"] == []


# ----------------------------------------------------------------------
# /search
# ----------------------------------------------------------------------


def test_search_happy_path_returns_scored_hits(
    populated_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The /search route's VaultSearch opens the FTS DB from _default_db_path()
    # and never builds an index itself (the real system indexes via the
    # AtomicWriter on writes / a bootstrap reindex). Point the search at an
    # isolated temp DB and index the populated vault into it, so this test is
    # hermetic instead of depending on the shared real data/jarvis.db.
    import sqlite3

    fts_index = pytest.importorskip("jarvis.memory.wiki.fts_index")
    db = tmp_path / "fts.db"
    monkeypatch.setattr(
        "jarvis.memory.wiki.search._default_db_path", lambda: db
    )
    conn = sqlite3.connect(str(db))
    try:
        fts_index.index_vault(populated_vault, conn)
    finally:
        conn.close()

    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/search", params={"q": "pizza"})
    body = r.json()
    assert body["ok"] is True
    assert body["query"] == "pizza"
    assert len(body["hits"]) >= 1
    top_hit = body["hits"][0]
    assert top_hit["slug"] == "ruben"
    assert 0.0 <= top_hit["score"] <= 1.0
    assert top_hit["path"].endswith(".md")
    assert "pizza" in top_hit["snippet"].lower()


def test_search_empty_query_returns_error_envelope(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/search", params={"q": ""})
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is False
    assert body["error"] == "empty query"


def test_search_with_fts5_syntax_chars_is_sanitised(populated_vault: Path) -> None:
    """Query containing FTS5 special chars must not raise; result OK envelope."""
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get(
            "/api/wiki/search",
            params={"q": 'pizza" AND (ruben*)'},
        )
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is True
    assert body["query"] == "pizza AND ruben"


def test_search_k_parameter_caps_results(populated_vault: Path) -> None:
    app = _make_app(populated_vault)
    with TestClient(app) as client:
        r = client.get("/api/wiki/search", params={"q": "is", "k": 1})
    body = r.json()
    assert body["ok"] is True
    assert len(body["hits"]) <= 1


# ----------------------------------------------------------------------
# Defensive: missing config
# ----------------------------------------------------------------------


def test_tree_without_config_returns_empty_ok(tmp_path: Path) -> None:
    """No ``app.state.config`` at all — must still return shape-correct JSON."""
    app = FastAPI()
    app.include_router(wiki_router)
    with TestClient(app) as client:
        r = client.get("/api/wiki/tree")
    body = r.json()
    assert body["ok"] is True
    assert body["stats"]["total_pages"] == 0


def test_page_without_config_returns_error_envelope() -> None:
    app = FastAPI()
    app.include_router(wiki_router)
    with TestClient(app) as client:
        r = client.get("/api/wiki/page/anything")
    body = r.json()
    assert body["ok"] is False
    assert "not configured" in body["error"]


# ----------------------------------------------------------------------
# /health (spec A5)
# ----------------------------------------------------------------------


def test_health_returns_200_with_fresh_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh ``WikiHealth`` singleton reports the all-unknown baseline shape.

    The singleton is process-wide, so other tests in the same run may have
    mutated it — replace it with a brand-new instance for this assertion
    rather than relying on run order for isolation.
    """
    from jarvis.memory.wiki.health import WikiHealth

    monkeypatch.setattr("jarvis.memory.wiki.health.health", WikiHealth())

    app = FastAPI()
    app.include_router(wiki_router)
    with TestClient(app) as client:
        r = client.get("/api/wiki/health")
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is True
    assert body["health"]["journal_backlog"] == 0
    assert body["health"]["bootstrap_ok"] is None
    assert body["health"]["last_write"] is None
    assert body["health"]["last_chain_failure"] is None
