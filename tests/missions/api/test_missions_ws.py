"""WebSocket-Tests fuer /api/missions/ws (Hello + Replay + Live-Fanout)."""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.missions.manager import MissionManager
from jarvis.ui.web.missions_auth import (
    issue_token,
    reset_tokens,
    router as missions_auth_router,
)
from jarvis.ui.web.missions_routes import router as missions_router
from jarvis.ui.web.missions_ws_routes import (
    ConnectionManager,
    router as missions_ws_router,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_tokens():
    reset_tokens()
    yield
    reset_tokens()


@pytest_asyncio.fixture
async def manager(tmp_path: Path):
    mgr = MissionManager(tmp_path / "missions.db")
    await mgr.start()
    try:
        yield mgr
    finally:
        await mgr.stop()


@pytest.fixture
def app(manager: MissionManager) -> FastAPI:
    """FastAPI mit voll verdrahtetem Mission-Stack (Auth + REST + WS)."""
    app = FastAPI()
    app.include_router(missions_auth_router)
    app.include_router(missions_router)
    app.include_router(missions_ws_router)
    app.state.mission_manager = manager
    conn_mgr = ConnectionManager()
    app.state.missions_ws_manager = conn_mgr
    manager.bus.subscribe_all(conn_mgr.fanout)
    return app


# ---------------------------------------------------------------------------
# Auth-Token-Endpoint
# ---------------------------------------------------------------------------


def test_auth_token_endpoint_returns_string(app: FastAPI) -> None:
    with TestClient(app) as client:
        r = client.get("/api/missions/auth/token")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["token"], str)
    assert len(body["token"]) > 20  # 32 bytes urlsafe ≈ 43 chars


# ---------------------------------------------------------------------------
# WS-Auth + Hello
# ---------------------------------------------------------------------------


def test_ws_closes_4400_on_missing_hello(app: FastAPI) -> None:
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/missions/ws") as ws:
                ws.send_json({"type": "not_hello"})
                # Server soll close(4400) schicken
                ws.receive_json()
    assert exc_info.value.code == 4400


def test_ws_closes_4401_on_invalid_token(app: FastAPI) -> None:
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/missions/ws") as ws:
                ws.send_json(
                    {"type": "hello", "last_seq": 0, "token": "bogus"}
                )
                ws.receive_json()
    assert exc_info.value.code == 4401


def test_ws_accepts_valid_token_and_streams_replay(
    app: FastAPI, manager: MissionManager
) -> None:
    """Dispatch einer Mission VOR dem WS-Connect → Replay liefert sie."""
    with TestClient(app) as client:
        d1 = client.post("/api/missions/dispatch", json={"prompt": "first"})
        d2 = client.post("/api/missions/dispatch", json={"prompt": "second"})
        assert d1.status_code == 201 and d2.status_code == 201

        token = client.get("/api/missions/auth/token").json()["token"]

        with client.websocket_connect("/api/missions/ws") as ws:
            ws.send_json({"type": "hello", "last_seq": 0, "token": token})
            # Replay: 2 Events (eines pro Mission, MissionDispatched)
            f1 = ws.receive_json()
            f2 = ws.receive_json()
    assert f1["payload"]["event_type"] == "MissionDispatched"
    assert f2["payload"]["event_type"] == "MissionDispatched"
    assert f1["payload"]["prompt"] == "first"
    assert f2["payload"]["prompt"] == "second"


def test_ws_replay_respects_last_seq(
    app: FastAPI, manager: MissionManager
) -> None:
    """``last_seq=1`` ueberspringt das erste Event und liefert nur das zweite."""
    with TestClient(app) as client:
        client.post("/api/missions/dispatch", json={"prompt": "first"})
        client.post("/api/missions/dispatch", json={"prompt": "second"})
        token = client.get("/api/missions/auth/token").json()["token"]

        with client.websocket_connect("/api/missions/ws") as ws:
            ws.send_json({"type": "hello", "last_seq": 1, "token": token})
            frame = ws.receive_json()
    assert frame["seq"] == 2
    assert frame["payload"]["prompt"] == "second"


def test_ws_live_fanout_after_connect(
    app: FastAPI, manager: MissionManager
) -> None:
    """Dispatch NACH dem Connect → Event landet via fanout im Client."""
    with TestClient(app) as client:
        token = client.get("/api/missions/auth/token").json()["token"]

        with client.websocket_connect("/api/missions/ws") as ws:
            ws.send_json({"type": "hello", "last_seq": 0, "token": token})
            # Jetzt eine Mission anstossen → Live-Frame muss kommen
            d = client.post(
                "/api/missions/dispatch", json={"prompt": "live!"}
            )
            assert d.status_code == 201
            mid = d.json()["mission_id"]
            frame = ws.receive_json()
    assert frame["mission_id"] == mid
    assert frame["payload"]["event_type"] == "MissionDispatched"
    assert frame["payload"]["prompt"] == "live!"
