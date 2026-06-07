"""Tests for the Socials section REST endpoints (CRUD + atomic JSON store).

Contract (see jarvis/ui/web/socials_routes.py):
- GET    /api/socials          → {"entries": [...]} sorted by ``order``.
                                  On first run (no data file) the store is
                                  seeded with Discord + the two GitHub links.
- POST   /api/socials          → create one; server assigns ``id`` (uuid hex)
                                  and ``order = max+1``; returns the entry (201).
- PATCH  /api/socials/{id}     → edit platform/label/url/enabled; returns it.
- DELETE /api/socials/{id}     → remove (idempotent → 200).

Storage is a dedicated JSON file under ``user_data_dir()/data/socials.json``
written atomically (tempfile + os.replace). It does NOT depend on the Brain, so
it works headless / with MockBrain — the tests use a bare app with only the
router, and redirect ``user_data_dir()`` via the ``LOCALAPPDATA`` env (read at
call time by jarvis.core.paths.user_data_dir, so this works cross-platform).

A ``javascript:`` (or any non-http(s)) URL must be rejected — the URL becomes an
``href`` in the UI, so the scheme allowlist is an XSS guard.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

DISCORD_URL = "https://discord.gg/UPu6pFWrJ"
REPO_URL = "https://github.com/PersonalJarvis/PersonalJarvis"
PROFILE_URL = "https://github.com/PersonalJarvis"
X_URL = "https://x.com/Alex_Sample"
JARVIS_X_URL = "https://x.com/PersonalJarvis"
INSTAGRAM_URL = "https://www.instagram.com/personaljarvis/"


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Redirect user_data_dir() → tmp so socials.json lands in the sandbox.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))


def _client() -> TestClient:
    """A fresh app with only the socials router — proves no Brain dependency."""
    from jarvis.ui.web.socials_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def client() -> TestClient:
    return _client()


# ----------------------------------------------------------------------
# Seed-on-first-run
# ----------------------------------------------------------------------


def test_get_seeds_on_first_run(client: TestClient) -> None:
    res = client.get("/api/socials")
    assert res.status_code == 200, res.text
    entries = res.json()["entries"]
    assert len(entries) == 6
    # Discord is the first (top) card.
    assert entries[0]["platform"] == "discord"
    assert entries[0]["url"] == DISCORD_URL
    # The two GitHub links, the two X links, and the Instagram profile follow.
    urls = [e["url"] for e in entries]
    assert REPO_URL in urls
    assert PROFILE_URL in urls
    assert X_URL in urls
    assert JARVIS_X_URL in urls  # the official project X account
    assert INSTAGRAM_URL in urls
    assert any(e["platform"] == "instagram" for e in entries)
    # X now has two links → it renders as a group (like GitHub).
    x_labels = [e["label"] for e in entries if e["platform"] == "x"]
    assert len(x_labels) == 2
    assert "Alex Sample" in x_labels
    assert "Personal Jarvis" in x_labels
    # Every entry carries the stable wire shape.
    for e in entries:
        assert set(e) >= {"id", "platform", "label", "url", "enabled", "order"}
        assert e["enabled"] is True


def test_seed_orders_are_contiguous_from_zero(client: TestClient) -> None:
    entries = client.get("/api/socials").json()["entries"]
    assert [e["order"] for e in entries] == [0, 1, 2, 3, 4, 5]


def test_seed_only_happens_once_not_after_emptying(client: TestClient) -> None:
    """Deleting every seed must NOT trigger a re-seed on the next GET."""
    entries = client.get("/api/socials").json()["entries"]
    for e in entries:
        assert client.delete(f"/api/socials/{e['id']}").status_code == 200
    after = client.get("/api/socials").json()["entries"]
    assert after == []


# ----------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------


def test_post_creates_entry_with_server_id_and_next_order(client: TestClient) -> None:
    client.get("/api/socials")  # seed
    res = client.post(
        "/api/socials",
        json={"platform": "x", "label": "X", "url": "https://x.com/jarvis"},
    )
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["id"]
    assert created["platform"] == "x"
    assert created["url"] == "https://x.com/jarvis"
    assert created["enabled"] is True
    assert created["order"] == 6  # after seeds 0,1,2,3,4,5

    entries = client.get("/api/socials").json()["entries"]
    assert any(e["id"] == created["id"] for e in entries)


def test_post_into_empty_store_gets_order_zero(client: TestClient) -> None:
    # Build directly without seeding: write an empty file first via delete-all.
    seed = client.get("/api/socials").json()["entries"]
    for e in seed:
        client.delete(f"/api/socials/{e['id']}")
    res = client.post(
        "/api/socials",
        json={"platform": "website", "label": "Site", "url": "https://example.com"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["order"] == 0


@pytest.mark.parametrize("bad_url", ["javascript:alert(1)", "ftp://x", "/relative", "mailto:a@b.c"])
def test_post_rejects_non_http_url(client: TestClient, bad_url: str) -> None:
    client.get("/api/socials")
    res = client.post(
        "/api/socials",
        json={"platform": "x", "label": "X", "url": bad_url},
    )
    assert res.status_code == 400, res.text


def test_post_requires_url_and_label(client: TestClient) -> None:
    client.get("/api/socials")
    # Missing url
    assert client.post("/api/socials", json={"platform": "x", "label": "X"}).status_code == 422
    # Empty label
    res = client.post(
        "/api/socials",
        json={"platform": "x", "label": "  ", "url": "https://x.com/a"},
    )
    assert res.status_code == 400, res.text


# ----------------------------------------------------------------------
# Update
# ----------------------------------------------------------------------


def test_patch_edits_fields(client: TestClient) -> None:
    discord = client.get("/api/socials").json()["entries"][0]
    res = client.patch(
        f"/api/socials/{discord['id']}",
        json={"label": "Unser Discord", "enabled": False},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["label"] == "Unser Discord"
    assert body["enabled"] is False
    # platform/url untouched
    assert body["platform"] == "discord"
    assert body["url"] == DISCORD_URL


def test_patch_rejects_bad_url(client: TestClient) -> None:
    discord = client.get("/api/socials").json()["entries"][0]
    res = client.patch(
        f"/api/socials/{discord['id']}",
        json={"url": "javascript:void(0)"},
    )
    assert res.status_code == 400, res.text


def test_patch_unknown_id_is_404(client: TestClient) -> None:
    client.get("/api/socials")
    assert client.patch("/api/socials/does-not-exist", json={"label": "x"}).status_code == 404


# ----------------------------------------------------------------------
# Delete
# ----------------------------------------------------------------------


def test_delete_removes_and_is_idempotent(client: TestClient) -> None:
    discord = client.get("/api/socials").json()["entries"][0]
    first = client.delete(f"/api/socials/{discord['id']}")
    assert first.status_code == 200
    assert first.json()["removed"] is True
    # Deleting again is a no-op 200.
    second = client.delete(f"/api/socials/{discord['id']}")
    assert second.status_code == 200
    assert second.json()["removed"] is False
    remaining = client.get("/api/socials").json()["entries"]
    assert all(e["id"] != discord["id"] for e in remaining)


# ----------------------------------------------------------------------
# Persistence across "restart"
# ----------------------------------------------------------------------


def test_changes_persist_across_fresh_app(client: TestClient) -> None:
    """A second app instance (simulating a restart) sees the same file."""
    client.get("/api/socials")  # seed
    created = client.post(
        "/api/socials",
        json={"platform": "youtube", "label": "YT", "url": "https://youtube.com/@jarvis"},
    ).json()

    fresh = _client()
    entries = fresh.get("/api/socials").json()["entries"]
    assert any(e["id"] == created["id"] for e in entries)


def test_store_file_is_written_under_data_dir(client: TestClient, tmp_path: Path) -> None:
    client.get("/api/socials")  # triggers seed write
    store = tmp_path / "Jarvis" / "data" / "socials.json"
    assert store.is_file()
