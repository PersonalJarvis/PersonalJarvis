"""Preparation HTTP contracts reject browser-supplied execution plans and stale approvals."""

from tests.integration.test_swarm_api import api as _api
from tests.integration.test_swarm_preparation import PreparationFactory

api = _api


def test_http_goal_questions_review_and_launch_only_stored_plan(api):
    client, service, _ = api
    service.brain_factory = PreparationFactory()
    draft = client.post(
        "/api/swarm/preparations",
        json={"name": "Goal", "goal": "Find the exact answer", "request_key": "goal"},
    )
    assert draft.status_code == 200, draft.text
    view = draft.json()
    base = f"/api/swarm/teams/{view['team']['id']}"
    assert view["state"] == "clarifying"
    assert client.get(base + "/preparation").json()["questions"] == view["questions"]
    assert client.post(base + "/start", json={}).status_code == 409
    assert client.get(base + "/tasks").json() == []
    body = {
        "expected_revision": view["revision"],
        "expected_storage_generation": "",
        "answers": {"deliverable": "The exact value"},
        "request_key": "answers",
    }
    answered = client.post(base + "/preparation/answers", json=body)
    assert answered.status_code == 200, answered.text
    ready = answered.json()
    assert ready["state"] == "ready"
    assert ready["team"]["state"] == "created"
    approval = {
        "expected_revision": ready["revision"],
        "expected_storage_generation": "",
        "digest": ready["digest"],
        "request_key": "launch",
    }
    assert client.post(base + "/launch", json={**approval, "tasks": []}).status_code == 422
    assert client.post(base + "/launch", json={**approval, "digest": "f" * 64}).status_code == 409
    launched = client.post(base + "/launch", json=approval)
    assert launched.status_code == 200, launched.text
    assert launched.json()["state"] == "launched"
    assert client.post(base + "/launch", json=approval).status_code == 200
    schema = client.get("/openapi.json").json()["paths"]
    for path in (
        "/api/swarm/preparations",
        "/api/swarm/teams/{team_id}/preparation",
        "/api/swarm/teams/{team_id}/preparation/answers",
        "/api/swarm/teams/{team_id}/launch",
    ):
        assert schema[path]["post"]["x-jarvis-dangerous"] is True


def test_http_approval_created_marker_and_question_retry(api):
    client, service, _ = api
    service.brain_factory = PreparationFactory()
    created = client.post(
        "/api/swarm/teams",
        json={
            "name": "Pending",
            "goal": "42",
            "request_key": "pending",
            "preparation_required": True,
        },
    ).json()
    base = f"/api/swarm/teams/{created['id']}"
    saved = client.get(base + "/preparation").json()
    assert saved["revision"] == 0 and not saved["busy"]
    assert saved["questions"] == [] and service.brain_factory.requests == []
    assert client.post(base + "/resume", json={}).status_code == 409
    ready = client.post(
        base + "/preparation", json={"expected_storage_generation": "", "request_key": "begin"}
    )
    assert ready.status_code == 200 and ready.json()["questions"]
