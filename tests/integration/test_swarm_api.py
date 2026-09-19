"""Authenticated HTTP and WebSocket behavior over the real local Swarm runtime."""

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from functools import partial

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.ui.web.surface_security import SurfaceSecurity
from jarvis.ui.web.swarm_routes import router
from tests.fakes.swarm_runtime import runtime, terminal


@pytest.fixture
def api(tmp_path):
    gate = asyncio.Event()
    service = runtime(tmp_path / "swarm", gate=gate)

    @asynccontextmanager
    async def lifespan(app):
        app.state.swarm = service
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    app.add_middleware(SurfaceSecurity, trusted_hosts="testserver")
    with TestClient(app) as client:
        yield client, service, gate


def create(client, name="API team", *, tasks=None):
    body = {"name": name, "goal": "42", "request_key": name}
    if tasks is not None:
        body["tasks"] = tasks
    response = client.post("/api/swarm/teams", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def control(client, team, action, *, version=None):
    body = {"expected_version": version} if version is not None else {}
    response = client.post(f"/api/swarm/teams/{team['id']}/{action}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_create_list_controls_and_stale_version_are_durable(api):
    client, service, gate = api
    team = create(client)
    assert create(client)["id"] == team["id"]
    assert client.get("/api/swarm/teams").json()[0]["id"] == team["id"]
    running = control(client, team, "start", version=team["version"])
    client.portal.call(partial(asyncio.wait_for, service.brain_factory.started.wait(), timeout=10))
    stale = client.post(
        f"/api/swarm/teams/{team['id']}/pause", json={"expected_version": team["version"]}
    )
    assert stale.status_code == 409, stale.text
    assert client.get(f"/api/swarm/teams/{team['id']}").json()["state"] == "running"
    paused = control(client, team, "pause")
    assert paused["state"] == "paused" and paused["version"] > running["version"]
    resumed = control(client, team, "resume", version=paused["version"])
    assert resumed["state"] == "running"
    stopped = control(client, team, "stop")
    assert stopped["state"] == "canceled"
    client.portal.call(gate.set)
    world = client.get(f"/api/swarm/teams/{team['id']}/world").json()
    assert world["team"]["state"] == "canceled"
    # The interrupted call had no usage receipt. Its original reservation must
    # remain conservative even though resumed work completed successfully.
    assert int(world["team"]["tokens_reserved"]) > 0
    assert world["team"]["lead_id"] == team["lead_id"]
    assert control(client, team, "archive")["state"] == "archived"


def test_records_are_paginated_and_indexed_inspector_reaches_later_tasks(api):
    client, _, _ = api
    description = "Detailed task evidence " * 80
    tasks = [
        {
            "id": f"task-{i}",
            "title": f"Task {i}",
            "description": description,
            "acceptance": "Retain all text in the inspector",
        }
        for i in range(25)
    ]
    team = create(client, tasks=tasks)
    base = f"/api/swarm/teams/{team['id']}"
    first = client.get(f"{base}/tasks?limit=2").json()
    second = client.get(f"{base}/tasks?limit=2&offset=2").json()
    assert [task["id"] for task in first + second] == [f"task-{i}" for i in range(4)]
    detail = client.get(f"{base}/tasks/record/task-24")
    assert detail.status_code == 200
    assert detail.json()["description"] == description
    lead = client.get(f"{base}/agents/record/{team['lead_id']}")
    assert lead.status_code == 200
    assert lead.json()["team_id"] == team["id"]
    world = client.get(f"{base}/world").json()
    assert len(world["tasks"]) <= 20
    assert world["counts"]["tasks"] == "25"
    assert all(len(task["description"]) <= 200 for task in world["tasks"])
    assert client.get(f"{base}/tasks?limit=201").status_code == 422
    assert client.get(f"{base}/tasks?offset=-1").status_code == 422
    assert client.get(f"{base}/tasks/record/missing").status_code == 403


def test_publish_route_retains_only_selected_result_after_team_cleanup(api):
    client, service, gate = api
    team = create(client)
    foreign = create(client, "Other team")
    control(client, team, "start")
    client.portal.call(gate.set)
    final = client.portal.call(terminal, service, team["id"])
    assert final["state"] == "succeeded", final
    base = f"/api/swarm/teams/{team['id']}"
    delivery = client.get(f"{base}/tasks/record/__swarm_delivery").json()
    selected = delivery["evidence"][0]
    artifact = client.get(f"{base}/artifacts/{selected}")
    assert artifact.status_code == 200
    assert json.loads(artifact.content) == {"value": 42}
    assert artifact.headers["x-content-type-options"] == "nosniff"
    assert artifact.headers["content-security-policy"] == "sandbox"
    assert client.get(f"/api/swarm/teams/{foreign['id']}/artifacts/{selected}").status_code == 403
    body = {"artifact_id": selected, "request_key": "selected-result"}
    stale = client.post(f"{base}/publish", json=dict(body, expected_version=team["version"]))
    assert stale.status_code == 409
    response = client.post(f"{base}/publish", json=body)
    assert response.status_code == 200, response.text
    publication = response.json()
    assert publication["state"] == "published"
    assert client.post(f"{base}/publish", json=body).json()["id"] == publication["id"]
    assert len(client.get(f"{base}/publications").json()) == 1
    retained_url = f"/api/swarm/publications/{publication['id']}"
    assert client.get(retained_url).content == artifact.content
    control(client, team, "archive")
    client.portal.call(service.storage.call, service.registry.local.delete, team["id"])
    assert client.get(base).status_code == 403
    assert client.get(retained_url).content == artifact.content
    assert client.get(f"/api/swarm/publications/{selected}").status_code == 403
    assert client.get(f"/api/swarm/teams/{foreign['id']}").status_code == 200


def test_backup_is_restorable_and_recovery_preserves_paused_team(api, tmp_path):
    client, service, _ = api
    team = create(client)
    control(client, team, "start")
    client.portal.call(partial(asyncio.wait_for, service.brain_factory.started.wait(), timeout=10))
    control(client, team, "pause")
    backup = control(client, team, "backup")
    assert backup["team_id"] == team["id"]
    assert "path" not in backup
    download = client.get(backup["download_url"])
    assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
    response = client.post(
        "/api/swarm/restores",
        files={"file": ("team.zip", download.content, "application/zip")},
        data={"request_key": "api-backup", "replace_team_id": team["id"]},
    )
    assert response.status_code == 200, response.text
    restored_team = response.json()["team"]
    assert restored_team["id"] == team["id"]
    assert restored_team["lead_id"] == team["lead_id"]
    recovered = control(client, team, "recover")
    assert recovered["state"] == "paused"
    assert recovered["lead_id"] == team["lead_id"]
    with sqlite3.connect(service.registry.open(team["id"]).path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_websocket_first_and_reconnect_snapshots_are_scoped_and_bounded(api):
    client, _, _ = api
    team = create(client)
    path = f"/api/swarm/teams/{team['id']}/ws"
    with client.websocket_connect(path) as socket:
        encoded = socket.receive_text()
        first = json.loads(encoded)
        assert len(encoded.encode("utf-8")) <= 65536
        assert first["team_id"] == team["id"]
        assert first["type"] == "snapshot"
        assert first["snapshot"]["team"]["id"] == team["id"]
    revision = first["snapshot"]["revision"]
    with client.websocket_connect(f"{path}?after={revision}") as socket:
        reconnect = socket.receive_json()
        assert reconnect["snapshot"]["revision"] == revision
        assert reconnect["team_id"] == team["id"]
    control(client, team, "stop")
    with client.websocket_connect(f"{path}?after={revision}") as socket:
        changed = socket.receive_json()
        assert int(changed["snapshot"]["revision"]) > int(revision)
        assert changed["snapshot"]["team"]["state"] == "canceled"
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/api/swarm/teams/not-a-team/ws"):
            pass
    assert rejected.value.code == 1008


def test_surface_requires_authentication_before_entering_swarm_routes(api):
    client, service, _ = api
    client.cookies.clear()
    assert client.get("/api/swarm/teams").status_code == 401
    assert (
        client.post(
            "/api/swarm/teams", json={"name": "Blocked", "goal": "42", "request_key": "blocked"}
        ).status_code
        == 401
    )
    assert not service.root.exists()
