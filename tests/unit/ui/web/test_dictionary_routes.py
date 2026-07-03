"""REST route tests for the STT dictionary API (custom vocabulary CRUD)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.dictionary_routes import router as dictionary_router


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # DictionaryStore resolves user_data_dir() per request; sandbox it so the
    # tests never touch the real user profile.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = FastAPI()
    app.include_router(dictionary_router)
    return TestClient(app)


def test_list_empty(client: TestClient) -> None:
    r = client.get("/api/dictionary")
    assert r.status_code == 200
    assert r.json() == {"entries": []}


def test_create_and_list(client: TestClient) -> None:
    r = client.post(
        "/api/dictionary", json={"word": "GitHub", "misheard": ["Gitter"]}
    )
    assert r.status_code == 201
    created = r.json()
    assert created["word"] == "GitHub"
    assert created["misheard"] == ["Gitter"]
    assert created["id"]

    r2 = client.get("/api/dictionary")
    assert r2.status_code == 200
    entries = r2.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["word"] == "GitHub"


def test_create_plain_word_without_misheard(client: TestClient) -> None:
    r = client.post("/api/dictionary", json={"word": "Anthropic"})
    assert r.status_code == 201
    assert r.json()["misheard"] == []


def test_create_rejects_empty_and_duplicate(client: TestClient) -> None:
    assert client.post("/api/dictionary", json={"word": "   "}).status_code == 400
    assert client.post("/api/dictionary", json={"word": "Fable"}).status_code == 201
    r = client.post("/api/dictionary", json={"word": "fable"})
    assert r.status_code == 400
    assert "already" in r.json()["detail"]


def test_patch_updates_word_and_misheard(client: TestClient) -> None:
    created = client.post(
        "/api/dictionary", json={"word": "Ultrathink", "misheard": ["UltraSync"]}
    ).json()
    r = client.patch(
        f"/api/dictionary/{created['id']}",
        json={"misheard": ["UltraSync", "Ultra-think"]},
    )
    assert r.status_code == 200
    assert r.json()["misheard"] == ["UltraSync", "Ultra-think"]

    r2 = client.patch(f"/api/dictionary/{created['id']}", json={"word": "Ultra"})
    assert r2.status_code == 200
    assert r2.json()["word"] == "Ultra"
    # Misheard list survives a word-only patch.
    assert r2.json()["misheard"] == ["UltraSync", "Ultra-think"]


def test_patch_unknown_id_404(client: TestClient) -> None:
    r = client.patch("/api/dictionary/nope", json={"word": "X"})
    assert r.status_code == 404


def test_patch_duplicate_word_400(client: TestClient) -> None:
    client.post("/api/dictionary", json={"word": "Fable"})
    other = client.post("/api/dictionary", json={"word": "Opus"}).json()
    r = client.patch(f"/api/dictionary/{other['id']}", json={"word": "fable"})
    assert r.status_code == 400


def test_delete_idempotent(client: TestClient) -> None:
    created = client.post("/api/dictionary", json={"word": "Claude.md"}).json()
    r1 = client.delete(f"/api/dictionary/{created['id']}")
    assert r1.status_code == 200
    assert r1.json() == {"ok": True, "removed": True}
    r2 = client.delete(f"/api/dictionary/{created['id']}")
    assert r2.status_code == 200
    assert r2.json() == {"ok": True, "removed": False}
