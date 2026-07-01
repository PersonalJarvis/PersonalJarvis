"""Integration tests for /api/docs routes via FastAPI TestClient."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.docs.registry import DocRegistry
from jarvis.ui.web.docs_routes import router as docs_router


CONCEPT_MD = """---
title: "Concept: Router-Discipline"
slug: router-discipline
diataxis: explanation
status: active
phase: 5
tags: [brain, routing]
---

# Concept: Router-Discipline

Hauptjarvis ist Pure Dispatcher.

## Wann triggern

Direkt-Aktion via Sub-Jarvis-Spawn.
"""

HOWTO_MD = """---
title: "How-To: Add a provider"
slug: provider-add
diataxis: howto
status: active
phase: 4
tags: [brain, plugin]
---

# How-To

Schritt 1.
"""


@pytest.fixture
def doc_root(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "concept-routing.md").write_text(CONCEPT_MD, encoding="utf-8")
    (tmp_path / "docs" / "howto-provider.md").write_text(HOWTO_MD, encoding="utf-8")
    # Asset im selben Doc-Ordner
    (tmp_path / "docs" / "diagram.png").write_bytes(b"PNG-FAKE-CONTENT")
    return tmp_path


@pytest.fixture
def app_with_registry(doc_root: Path) -> FastAPI:
    """Minimal-FastAPI mit unserem Router + DocRegistry-State."""
    app = FastAPI()
    registry = DocRegistry(
        roots=[doc_root / "docs"],
        index_db=doc_root / "index.sqlite",
    )
    registry.reload_sync()
    app.state.doc_registry = registry
    app.include_router(docs_router)
    return app


@pytest.fixture
def client(app_with_registry: FastAPI) -> TestClient:
    return TestClient(app_with_registry)


# ----------------------------------------------------------------------
# /api/docs (List + Filter)
# ----------------------------------------------------------------------

def test_list_returns_all_docs(client: TestClient) -> None:
    resp = client.get("/api/docs")
    assert resp.status_code == 200
    data = resp.json()
    slugs = [d["slug"] for d in data]
    assert "router-discipline" in slugs
    assert "provider-add" in slugs


def test_list_filter_by_diataxis(client: TestClient) -> None:
    resp = client.get("/api/docs?diataxis=howto")
    assert resp.status_code == 200
    data = resp.json()
    assert all(d["diataxis"] == "howto" for d in data)
    assert any(d["slug"] == "provider-add" for d in data)


def test_list_filter_by_phase(client: TestClient) -> None:
    resp = client.get("/api/docs?phase=4")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["slug"] == "provider-add"


def test_list_filter_by_tags(client: TestClient) -> None:
    resp = client.get("/api/docs?tags=plugin")
    data = resp.json()
    slugs = [d["slug"] for d in data]
    assert "provider-add" in slugs
    assert "router-discipline" not in slugs


# ----------------------------------------------------------------------
# /api/docs/grouped
# ----------------------------------------------------------------------

def test_grouped_returns_diataxis_buckets(client: TestClient) -> None:
    resp = client.get("/api/docs/grouped")
    assert resp.status_code == 200
    data = resp.json()
    assert "explanation" in data
    assert "howto" in data
    assert len(data["howto"]) == 1


# ----------------------------------------------------------------------
# /api/docs/search
# ----------------------------------------------------------------------

def test_search_finds_match(client: TestClient) -> None:
    resp = client.get("/api/docs/search?q=Dispatcher")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["slug"] == "router-discipline"
    assert "<mark>" in data[0]["snippet"]


def test_search_with_filter(client: TestClient) -> None:
    resp = client.get("/api/docs/search?q=Schritt&diataxis=howto")
    data = resp.json()
    slugs = [d["slug"] for d in data]
    assert "provider-add" in slugs


def test_search_invalid_limit(client: TestClient) -> None:
    resp = client.get("/api/docs/search?q=foo&limit=0")
    assert resp.status_code == 400


def test_search_empty_query_returns_empty(client: TestClient) -> None:
    resp = client.get("/api/docs/search?q=   ")
    assert resp.status_code == 200
    assert resp.json() == []


# ----------------------------------------------------------------------
# /api/docs/{slug}
# ----------------------------------------------------------------------

def test_get_doc_returns_full_body(client: TestClient) -> None:
    resp = client.get("/api/docs/router-discipline")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "router-discipline"
    assert "Hauptjarvis ist Pure Dispatcher" in data["body"]
    assert any(h["slug"] == "wann-triggern" for h in data["headings"])


def test_get_doc_unknown_returns_404(client: TestClient) -> None:
    resp = client.get("/api/docs/does-not-exist")
    assert resp.status_code == 404


# ----------------------------------------------------------------------
# /api/docs/asset/{slug}/{path}
# ----------------------------------------------------------------------

def test_get_asset_returns_file(client: TestClient) -> None:
    resp = client.get("/api/docs/asset/router-discipline/diagram.png")
    assert resp.status_code == 200
    assert resp.content == b"PNG-FAKE-CONTENT"


def test_get_asset_path_traversal_blocked(client: TestClient) -> None:
    resp = client.get("/api/docs/asset/router-discipline/..%2F..%2Fetc%2Fpasswd")
    # Either 400 (traversal) or 404 — as long as it's not 200
    assert resp.status_code in (400, 404)


def test_get_asset_unknown_doc_404(client: TestClient) -> None:
    resp = client.get("/api/docs/asset/does-not-exist/diagram.png")
    assert resp.status_code == 404


# ----------------------------------------------------------------------
# /api/docs/reload
# ----------------------------------------------------------------------

def test_reload_picks_up_new_file(client: TestClient, doc_root: Path) -> None:
    new_md = """---
title: "ADR-0099"
slug: adr-0099-test
diataxis: adr
status: active
phase: 6
---

# ADR-0099
"""
    (doc_root / "docs" / "adr-0099.md").write_text(new_md, encoding="utf-8")
    resp = client.post("/api/docs/reload")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 3  # 2 originale + 1 neu
    # Verify durch List-Endpoint
    listed = client.get("/api/docs").json()
    slugs = [d["slug"] for d in listed]
    assert "adr-0099-test" in slugs


# ----------------------------------------------------------------------
# Registry fehlt -> 503
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# /api/docs/{slug}/open — Editor-Launch
# ----------------------------------------------------------------------

def test_open_doc_in_editor(client: TestClient, monkeypatch) -> None:
    """``os.startfile`` is Windows-only and non-blocking. We patch it
    away and only check that the endpoint resolves the path correctly.
    """
    import os as os_module

    calls: list[str] = []

    def fake_startfile(path: str) -> None:
        calls.append(path)

    monkeypatch.setattr(os_module, "startfile", fake_startfile, raising=False)
    resp = client.post("/api/docs/router-discipline/open")
    assert resp.status_code == 200
    data = resp.json()
    assert data["opened"] is True
    assert "concept-routing.md" in data["path"]
    assert len(calls) == 1


def test_open_doc_in_editor_unknown_slug(client: TestClient) -> None:
    resp = client.post("/api/docs/does-not-exist/open")
    assert resp.status_code == 404


# ----------------------------------------------------------------------
# Registry fehlt -> 503
# ----------------------------------------------------------------------

def test_503_when_registry_missing() -> None:
    app = FastAPI()
    app.state.doc_registry = None
    app.include_router(docs_router)
    client = TestClient(app)
    resp = client.get("/api/docs")
    assert resp.status_code == 503
