"""REST route tests for the Agentic IDE skill library (saved Markdown prompts)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide.skill_library import (
    HUES,
    SkillLibrary,
    derive_description,
    derive_title,
    skill_library_path,
)
from jarvis.ui.web.ide_skills_routes import router as ide_skills_router

BASE = "/api/agentic-ide/skills"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # The library resolves user_data_dir() per request; sandbox it so the tests
    # never touch the real user profile.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    app = FastAPI()
    app.include_router(ide_skills_router)
    return TestClient(app)


def test_list_starts_empty(client: TestClient) -> None:
    r = client.get(BASE)
    assert r.status_code == 200
    assert r.json() == {"skills": []}


def test_create_lists_newest_first_with_derived_summary(client: TestClient) -> None:
    first = client.post(BASE, json={"title": "Review", "content": "# Review\n\nCheck every diff."})
    assert first.status_code == 201
    body = first.json()
    assert body["title"] == "Review"
    assert body["description"] == "Check every diff."
    assert body["hue"] in HUES
    assert body["icon"] == "auto"
    assert body["use_count"] == 0

    client.post(BASE, json={"title": "Style", "content": "Short sentences."})
    titles = [skill["title"] for skill in client.get(BASE).json()["skills"]]
    assert titles == ["Style", "Review"]


def test_create_rejects_empty_title_or_text_and_unknown_choices(client: TestClient) -> None:
    assert client.post(BASE, json={"title": "  ", "content": "x"}).status_code == 400
    assert client.post(BASE, json={"title": "A", "content": " \n "}).status_code == 400
    assert client.post(BASE, json={"title": "A", "content": "x", "hue": "plaid"}).status_code == 400
    assert client.post(BASE, json={"title": "A", "content": "x", "icon": "nope"}).status_code == 400


def test_windows_line_endings_are_normalised(client: TestClient) -> None:
    created = client.post(BASE, json={"title": "A", "content": "one\r\ntwo\rthree"}).json()
    assert created["content"] == "one\ntwo\nthree"


def test_patch_get_and_delete(client: TestClient) -> None:
    skill = client.post(BASE, json={"title": "A", "content": "first text"}).json()
    r = client.patch(
        f"{BASE}/{skill['id']}",
        json={"title": "B", "content": "new body", "hue": "rose", "icon": "bug"},
    )
    assert r.status_code == 200
    updated = r.json()
    assert (updated["title"], updated["content"], updated["hue"], updated["icon"]) == (
        "B",
        "new body",
        "rose",
        "bug",
    )
    assert updated["description"] == "new body"
    assert client.get(f"{BASE}/{skill['id']}").json()["title"] == "B"

    assert client.delete(f"{BASE}/{skill['id']}").json() == {"ok": True, "removed": True}
    assert client.delete(f"{BASE}/{skill['id']}").json() == {"ok": True, "removed": False}
    assert client.get(f"{BASE}/{skill['id']}").status_code == 404
    assert client.patch(f"{BASE}/{skill['id']}", json={"title": "C"}).status_code == 404


def test_used_counts_pastes_without_touching_updated_at(client: TestClient) -> None:
    skill = client.post(BASE, json={"title": "A", "content": "text"}).json()
    client.post(f"{BASE}/{skill['id']}/used")
    used = client.post(f"{BASE}/{skill['id']}/used").json()
    assert used["use_count"] == 2
    assert used["last_used_at"]
    assert used["updated_at"] == skill["updated_at"]
    assert client.post(f"{BASE}/missing/used").status_code == 404


def test_order_puts_named_ids_first(client: TestClient) -> None:
    ids = [client.post(BASE, json={"title": t, "content": t}).json()["id"] for t in "abc"]
    # Library order is newest first: c, b, a.
    r = client.put(f"{BASE}/order", json={"ids": [ids[0], "unknown"]})
    assert [skill["title"] for skill in r.json()["skills"]] == ["a", "c", "b"]


def test_derive_reads_frontmatter_heading_and_file_name(client: TestClient) -> None:
    skill_md = "---\nname: pr-review\ndescription: >-\n  Review a pull\n  request.\n---\n# Ignored"
    r = client.post(f"{BASE}/derive", json={"content": skill_md, "filename": "SKILL.md"})
    assert r.json() == {"title": "pr-review", "description": "Review a pull request."}

    plain = {"content": "plain words\n", "filename": "house_style.md"}
    r = client.post(f"{BASE}/derive", json=plain)
    assert r.json()["title"] == "house style"


def test_unreadable_sidecar_degrades_to_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    path = skill_library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert SkillLibrary().list_all() == []


def test_derive_helpers_skip_fences_and_tables() -> None:
    text = "# Title\n\n```\ncode\n```\n| a | b |\n> Quoted *lead* line\n"
    assert derive_title(text) == "Title"
    assert derive_description(text) == "Quoted *lead* line"
