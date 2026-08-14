"""Unit tests for the screenshot-blob route (/api/screenshots/{sha256}).

The route serves flight-recorder frames by content hash. The hash IS the
capability: strict hex validation is the whole path-traversal defence, so it
gets its own tests.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web import screenshots_routes


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(screenshots_routes, "_BLOB_DIR", tmp_path)
    app = FastAPI()
    app.include_router(screenshots_routes.router)
    return TestClient(app)


_HASH = "a" * 64


def test_serves_jpg_blob_by_hash(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / f"{_HASH}.jpg").write_bytes(b"\xff\xd8fakejpeg")
    client = _client(tmp_path, monkeypatch)
    resp = client.get(f"/api/screenshots/{_HASH}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert "immutable" in resp.headers["cache-control"]
    assert resp.content.startswith(b"\xff\xd8")


def test_unknown_hash_is_404(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get(f"/api/screenshots/{_HASH}").status_code == 404


def test_non_hash_input_is_rejected(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    # Too short and wrong alphabet: refused by the hash check (400).
    assert client.get("/api/screenshots/abc").status_code == 400
    assert client.get(f"/api/screenshots/{'z' * 64}").status_code == 400
    # Traversal shape: the decoded slash makes it a different route, so the
    # ROUTER refuses it (404) before the handler ever runs. What matters is
    # that the file content never leaves — not which layer said no.
    traversal = client.get("/api/screenshots/..%2Fsecret.txt")
    assert traversal.status_code in (400, 404)
    assert b"nope" not in traversal.content
