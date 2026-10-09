"""Saved companion designs: stored for any agent to wear, deduplicated,
capped, tolerant of a damaged file, and reachable over the society API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.society import design_library
from jarvis.society.companion import CompanionSkin
from jarvis.ui.web.society_routes import router

GALAXY = {
    "colors": ["#e05cc5", "#6a2fc2", "#160f3d"],
    "pattern": "radial",
    "angle": 135,
    "effect": "stars",
    "name": "Galaxy",
}


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path


def test_nothing_saved_lists_nothing() -> None:
    assert design_library.list_designs() == []


def test_saved_design_round_trips_newest_first() -> None:
    first = design_library.save_design(CompanionSkin.model_validate(GALAXY))
    second = design_library.save_design(
        CompanionSkin.model_validate({"colors": ["#ffffff", "#000000"], "name": "Ink"})
    )
    designs = design_library.list_designs()
    assert [d["id"] for d in designs] == [second["id"], first["id"]]
    assert designs[1]["skin"] == GALAXY


def test_saving_the_same_look_again_renames_instead_of_duplicating() -> None:
    first = design_library.save_design(CompanionSkin.model_validate(GALAXY))
    again = design_library.save_design(
        CompanionSkin.model_validate({**GALAXY, "colors": ["#E05CC5", "#6a2fc2", "#160f3d"],
                                      "name": "Milky Way"})
    )
    designs = design_library.list_designs()
    assert again["id"] == first["id"]
    assert [d["skin"]["name"] for d in designs] == ["Milky Way"]


def test_the_collection_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(design_library, "MAX_DESIGNS", 3)
    for angle in range(5):
        design_library.save_design(CompanionSkin.model_validate({**GALAXY, "angle": angle}))
    assert [d["skin"]["angle"] for d in design_library.list_designs()] == [4, 3, 2]


def test_delete_removes_one_design() -> None:
    saved = design_library.save_design(CompanionSkin.model_validate(GALAXY))
    assert design_library.delete_design(saved["id"]) is True
    assert design_library.delete_design(saved["id"]) is False
    assert design_library.list_designs() == []


def test_a_damaged_entry_is_skipped_and_a_broken_file_reads_empty() -> None:
    path = design_library._path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"designs": [
        {"id": "dbad", "skin": {"colors": ["#fff"]}},
        {"id": "dgood", "skin": GALAXY, "created": 1.0},
        "junk",
    ]}), encoding="utf-8")
    assert [d["id"] for d in design_library.list_designs()] == ["dgood"]
    path.write_text("{not json", encoding="utf-8")
    assert design_library.list_designs() == []


def test_designs_api_saves_lists_and_deletes() -> None:
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    saved = client.post("/api/society/designs", json=GALAXY)
    assert saved.status_code == 200
    design_id = saved.json()["id"]
    assert client.get("/api/society/designs").json()["designs"][0]["skin"] == GALAXY
    assert client.post("/api/society/designs", json={"colors": ["#123456"]}).status_code == 422
    assert client.delete(f"/api/society/designs/{design_id}").status_code == 200
    assert client.delete(f"/api/society/designs/{design_id}").status_code == 404
