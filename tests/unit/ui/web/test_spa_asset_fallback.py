"""The SPA fallback must never answer a missing ASSET with index.html.

An <img>/<script>/<link> that receives HTML with a 200 renders as permanently
broken — the browser records the load as a success and never retries. That is
exactly how the sidebar logo froze into the broken-image glyph during a
mid-rebuild dist window (2026-07-18). Missing files with a build-shipped asset
extension get an honest 404; extensionless client-side routes still fall back
to index.html so deep links keep working.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import jarvis.ui.web.server as server_mod


def _client(tmp_path, monkeypatch) -> tuple[TestClient, object]:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><body><div id=root></div></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setattr(server_mod, "DIST_DIR", dist)
    monkeypatch.setattr(server_mod, "INDEX_FILE", dist / "index.html")
    monkeypatch.setattr(server_mod, "ASSETS_DIR", dist / "assets")

    app = FastAPI()
    stub = SimpleNamespace(cfg=SimpleNamespace(ui=SimpleNamespace(dev_mode=False)))
    stub._spa_index_response = lambda: server_mod.WebServer._spa_index_response(stub)  # type: ignore[arg-type]
    server_mod.WebServer._register_static_or_spa(stub, app)  # type: ignore[arg-type]
    return TestClient(app), dist


def test_missing_asset_gets_honest_404_not_index_html(tmp_path, monkeypatch) -> None:
    client, _dist = _client(tmp_path, monkeypatch)
    response = client.get("/jarvis-logo.png")
    assert response.status_code == 404
    assert b"<div id=root>" not in response.content


def test_client_side_route_still_falls_back_to_index(tmp_path, monkeypatch) -> None:
    client, _dist = _client(tmp_path, monkeypatch)
    response = client.get("/wiki/some/page")
    assert response.status_code == 200
    assert b"<div id=root>" in response.content


def test_existing_asset_is_served_as_is(tmp_path, monkeypatch) -> None:
    client, dist = _client(tmp_path, monkeypatch)
    (dist / "jarvis-logo.png").write_bytes(b"\x89PNG\r\n\x1a\n fake-png-body")
    response = client.get("/jarvis-logo.png")
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")
