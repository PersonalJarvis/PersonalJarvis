"""Tests for PUT /api/profile/raw — hand-editing USER.md from the desktop UI.

Contract (see jarvis/ui/web/profile_routes.py::put_raw):
- writes the text verbatim and atomically (no re-render through frontmatter)
- reloads the in-memory UserProfile so GET /api/profile reflects the edit
- optimistic-concurrency: a stale ``mtime_ms`` is rejected with 409
- malformed YAML frontmatter degrades to ``frontmatter_ok: false`` (never a crash)
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.memory.user_profile import UserProfile
from jarvis.ui.web.profile_routes import router

INITIAL = """\
---
identity:
  name: Alt
  languages:
    - Deutsch
communication:
  directness: 5
---

# USER.md

## Observations
- baseline
"""


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    user_md = tmp_path / "USER.md"
    user_md.write_text(INITIAL, encoding="utf-8")
    profile = UserProfile.load(user_md)

    app = FastAPI()
    app.include_router(router)
    app.state.brain = types.SimpleNamespace(
        _user_profile=profile,
        _curator=None,
        _people=None,
    )
    return TestClient(app)


def test_put_raw_writes_and_reloads(client: TestClient) -> None:
    raw = client.get("/api/profile/raw").json()
    assert raw["content"].startswith("---")
    assert raw["path"] == "USER.md"

    new_content = raw["content"].replace("name: Alt", "name: Neu")
    res = client.put(
        "/api/profile/raw",
        json={"content": new_content, "mtime_ms": raw["mtime_ms"]},
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["ok"] is True
    assert payload["frontmatter_ok"] is True
    assert payload["reparsed"] is True
    assert payload["path"] == "USER.md"

    # File on disk reflects the edit…
    assert client.get("/api/profile/raw").json()["content"] == new_content
    # …and so does the parsed in-memory profile (cluster cards).
    assert client.get("/api/profile").json()["user"]["name"] == "Neu"


def test_put_raw_rejects_stale_mtime(client: TestClient) -> None:
    raw = client.get("/api/profile/raw").json()
    res = client.put(
        "/api/profile/raw",
        # A timestamp well before the file's real mtime → someone/something
        # else must have changed it since the client loaded.
        json={"content": raw["content"] + "\nedit\n", "mtime_ms": 1},
    )
    assert res.status_code == 409


def test_put_raw_without_mtime_skips_guard(client: TestClient) -> None:
    # Omitting mtime_ms opts out of the concurrency guard (force-write).
    res = client.put("/api/profile/raw", json={"content": "no frontmatter here"})
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_put_raw_flags_broken_frontmatter(client: TestClient) -> None:
    broken = "---\nidentity: : : not valid yaml\n---\n\nbody\n"
    res = client.put("/api/profile/raw", json={"content": broken})
    assert res.status_code == 200
    payload = res.json()
    # Lenient parse → empty meta → we flag it so the UI can warn.
    assert payload["frontmatter_ok"] is False
