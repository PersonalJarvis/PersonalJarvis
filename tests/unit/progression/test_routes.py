"""``/api/progression``: one read for the Verse, one metered write for world actions."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.progression.rules import MAX_LEVEL, REWARDS, RULES
from jarvis.progression.service import ProgressionService
from jarvis.ui.web.progression_routes import router


@pytest.fixture
def client(tmp_path: Path):
    app = FastAPI()
    app.include_router(router)
    service = ProgressionService(tmp_path / "progression.db", pet_id=lambda: "miso")
    app.state.progression = service
    with TestClient(app) as test_client:
        yield test_client
    service.close()


def test_the_first_read_carries_the_whole_rulebook(client: TestClient):
    body = client.get("/api/progression").json()
    assert body["subjects"] == []
    assert body["pet_id"] == "miso"
    assert body["max_level"] == MAX_LEVEL
    assert len(body["level_xp"]) == MAX_LEVEL and body["level_xp"][0] == 0
    assert {r["source"] for r in body["rules"]} == {r.source for r in RULES}
    assert {r["reward_id"] for r in body["rewards"]} == {r.reward_id for r in REWARDS}
    assert body["titles"]["person"][0] == {"level": 1, "title": "private"}


def test_a_world_action_pays_and_shows_up_in_the_next_read(client: TestClient):
    action = {"action": "floor_discovered", "ref": "arcade"}
    paid = client.post("/api/progression/actions", json=action)
    assert paid.status_code == 200
    award = paid.json()["awarded"]
    assert (award["subject_id"], award["xp"]) == ("person", 20)
    again = client.post("/api/progression/actions", json=action)
    assert again.json() == {"awarded": None}

    body = client.get("/api/progression").json()
    assert body["subjects"][0]["subject_id"] == "person"
    assert body["subjects"][0]["xp_into_level"] == 20
    assert body["subjects"][0]["xp_for_next"] == 40
    assert body["latest_seq"] == award["seq"]
    assert client.get(f"/api/progression?after_seq={award['seq']}").json()["recent"] == []


def test_server_only_and_unknown_actions_are_refused(client: TestClient):
    for action in ("chat_turn", "level_up_now"):
        assert client.post("/api/progression/actions", json={"action": action}).status_code == 422


def test_without_the_service_the_api_says_unavailable():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        assert test_client.get("/api/progression").status_code == 503
