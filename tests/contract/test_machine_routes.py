"""The real outer security boundary permits only the dedicated connector handshake."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.machines.models import JobState
from jarvis.machines.store import SCHEMA
from jarvis.ui.web.machines_routes import router
from jarvis.ui.web.surface_security import SurfaceSecurity

pytestmark = pytest.mark.no_auto_web_auth


@pytest.fixture
def machine_client(tmp_path):
    async def started():
        return None

    app = FastAPI()
    app.state.society = SimpleNamespace(data_dir=tmp_path, ensure_started=started)
    app.include_router(router)
    guarded = SurfaceSecurity(app, control_key_validator=lambda token: token == "test-control")  # noqa: S105 -- isolated test credential
    with TestClient(guarded, base_url="http://127.0.0.1", client=("127.0.0.1", 45000)) as client:
        yield client


def hello(code=None, token=None):
    return {"version": 1, "pairing_code": code, "token": token, "capabilities": {"os": "linux"}}


def test_connector_pair_reconnect_and_revoke(machine_client):
    client = machine_client
    response = client.post(
        "/api/machines/pairing",
        json={"name": "VPS"},
        headers={"Authorization": "Bearer test-control"},
    )
    assert response.status_code == 200, response.text
    code = response.json()["code"]
    with client.websocket_connect("wss://127.0.0.1/api/machines/connect") as socket:
        socket.send_json(hello(code=code))
        welcome = socket.receive_json()
        token = welcome["token"]
        machine_id = welcome["machine_id"]
        assert client.get("/api/machines", headers={"Authorization": "Bearer test-control"}).json()[
            "machines"
        ][0]["online"]
        # A device credential cannot create new credentials or manage other hosts.
        assert (
            client.post(
                "/api/machines/pairing",
                json={"name": "stolen"},
                headers={"Authorization": "Bearer " + token},
            ).status_code
            == 401
        )
        socket.send_json({"type": "heartbeat"})
        assert socket.receive_json()["seconds"] == 30
    with client.websocket_connect("wss://127.0.0.1/api/machines/connect") as socket:
        socket.send_json(hello(token=token))
        assert socket.receive_json()["machine_id"] == machine_id
    assert (
        client.delete(
            "/api/machines/" + machine_id, headers={"Authorization": "Bearer test-control"}
        ).status_code
        == 200
    )
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("wss://127.0.0.1/api/machines/connect") as socket:
            socket.send_json(hello(token=token))
            socket.receive_json()


def test_browser_cannot_open_connector_socket(machine_client):
    with pytest.raises(WebSocketDisconnect):
        with machine_client.websocket_connect(
            "wss://127.0.0.1/api/machines/connect", headers={"Origin": "http://127.0.0.1"}
        ) as socket:
            socket.receive_json()


def test_machine_job_enum_parity():
    root = Path(__file__).resolve().parents[2]
    ts = (root / "jarvis/ui/web/frontend/src/lib/machinesApi.ts").read_text(encoding="utf-8")
    states = re.search(r"MACHINE_JOB_STATES = \[(.*?)\]", ts, re.S).group(1)
    assert set(re.findall(r'"([a-z]+)"', states)) == {state.value for state in JobState}
    for state in JobState:
        assert f"'{state.value}'" in SCHEMA
