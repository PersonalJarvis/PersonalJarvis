"""Tests for the Contacts section REST endpoints (CRUD + validation).

Contract (see jarvis/ui/web/contacts_routes.py):
- GET    /api/contacts          → {"contacts": [summary, ...]} sorted by name.
- GET    /api/contacts/{slug}   → full contact dict, or 404.
- POST   /api/contacts          → create one (server derives slug); returns full (201).
- PATCH  /api/contacts/{slug}   → partial edit; returns full; 404 if unknown.
- DELETE /api/contacts/{slug}   → remove (idempotent → 200, {"removed": bool}).

The store is one ``<slug>.md`` file per contact under
``user_data_dir()/data/contacts/`` written atomically. It does NOT depend on the
Brain, so a bare app with only this router works headless. ``user_data_dir()``
is redirected via the ``LOCALAPPDATA`` env (read at call time), so the test runs
in a tmp sandbox cross-platform.

Validation contract:
- unknown ``relationship`` → 422 (Pydantic Literal),
- malformed e-mail / phone → 400 (store raises ValueError),
- empty name → 400; missing name → 422,
- an oversized README → 400.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))


def _client() -> TestClient:
    from jarvis.ui.web.contacts_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def client() -> TestClient:
    return _client()


def _make(client: TestClient, **overrides: object) -> dict:
    body = {
        "name": "Christoph Meyer",
        "relationship": "friend",
        "emails": ["christoph@example.com"],
        "phones": ["+49 151 2345 6789"],
        "address": {"city": "Berlin", "postal_code": "10115"},
        "note": "My oldest friend.",
    }
    body.update(overrides)
    return client.post("/api/contacts", json=body).json()


# ----------------------------------------------------------------------
# Empty / list
# ----------------------------------------------------------------------


def test_list_is_empty_on_first_run(client: TestClient) -> None:
    res = client.get("/api/contacts")
    assert res.status_code == 200, res.text
    assert res.json() == {"contacts": []}


# ----------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------


def test_post_creates_contact(client: TestClient) -> None:
    res = client.post(
        "/api/contacts",
        json={
            "name": "Christoph Meyer",
            "relationship": "friend",
            "emails": ["christoph@example.com"],
            "phones": ["+49 151 2345 6789"],
            "address": {"city": "Berlin"},
            "note": "Oldest friend.",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["slug"] == "christoph_meyer"
    assert body["name"] == "Christoph Meyer"
    assert body["relationship"] == "friend"
    assert body["emails"] == ["christoph@example.com"]
    assert body["phones"] == ["+4915123456789"]  # normalised
    assert body["address"]["city"] == "Berlin"
    assert body["primary_email"] == "christoph@example.com"


def test_created_contact_appears_in_list_and_detail(client: TestClient) -> None:
    created = _make(client)
    listed = client.get("/api/contacts").json()["contacts"]
    assert any(c["slug"] == created["slug"] for c in listed)
    # Summaries do not carry the README/full address.
    summary = next(c for c in listed if c["slug"] == created["slug"])
    assert "note" not in summary

    detail = client.get(f"/api/contacts/{created['slug']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["note"].strip() == "My oldest friend."


def test_post_rejects_unknown_relationship_422(client: TestClient) -> None:
    res = client.post("/api/contacts", json={"name": "X", "relationship": "enemy"})
    assert res.status_code == 422, res.text


def test_post_rejects_malformed_email_400(client: TestClient) -> None:
    res = client.post("/api/contacts", json={"name": "X", "emails": ["not-an-email"]})
    assert res.status_code == 400, res.text


def test_post_rejects_malformed_phone_400(client: TestClient) -> None:
    res = client.post("/api/contacts", json={"name": "X", "phones": ["abc"]})
    assert res.status_code == 400, res.text


def test_post_missing_name_422(client: TestClient) -> None:
    assert client.post("/api/contacts", json={"relationship": "friend"}).status_code == 422


def test_post_empty_name_400(client: TestClient) -> None:
    assert client.post("/api/contacts", json={"name": "   "}).status_code == 400


def test_post_rejects_oversized_readme_400(client: TestClient) -> None:
    res = client.post("/api/contacts", json={"name": "X", "note": "a" * 100_000})
    assert res.status_code == 400, res.text


# ----------------------------------------------------------------------
# Update
# ----------------------------------------------------------------------


def test_patch_edits_fields(client: TestClient) -> None:
    created = _make(client)
    res = client.patch(
        f"/api/contacts/{created['slug']}",
        json={"relationship": "colleague", "note": "Now a colleague."},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["relationship"] == "colleague"
    assert body["note"].strip() == "Now a colleague."
    # Untouched fields persist.
    assert body["emails"] == ["christoph@example.com"]


def test_patch_can_replace_emails(client: TestClient) -> None:
    created = _make(client)
    res = client.patch(
        f"/api/contacts/{created['slug']}",
        json={"emails": ["new@example.com"]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["emails"] == ["new@example.com"]


def test_patch_unknown_slug_404(client: TestClient) -> None:
    assert client.patch("/api/contacts/nope", json={"note": "x"}).status_code == 404


def test_patch_rejects_bad_email_400(client: TestClient) -> None:
    created = _make(client)
    res = client.patch(f"/api/contacts/{created['slug']}", json={"emails": ["bad"]})
    assert res.status_code == 400, res.text


# ----------------------------------------------------------------------
# Delete
# ----------------------------------------------------------------------


def test_delete_removes_and_is_idempotent(client: TestClient) -> None:
    created = _make(client)
    first = client.delete(f"/api/contacts/{created['slug']}")
    assert first.status_code == 200
    assert first.json()["removed"] is True
    second = client.delete(f"/api/contacts/{created['slug']}")
    assert second.status_code == 200
    assert second.json()["removed"] is False
    assert client.get(f"/api/contacts/{created['slug']}").status_code == 404


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------


def test_changes_persist_across_fresh_app(client: TestClient) -> None:
    created = _make(client, name="Persisted Person")
    fresh = _client()
    listed = fresh.get("/api/contacts").json()["contacts"]
    assert any(c["slug"] == created["slug"] for c in listed)


def test_store_file_is_written_under_data_dir(client: TestClient, tmp_path: Path) -> None:
    created = _make(client, name="On Disk")
    md = tmp_path / "Jarvis" / "data" / "contacts" / f"{created['slug']}.md"
    assert md.is_file()
