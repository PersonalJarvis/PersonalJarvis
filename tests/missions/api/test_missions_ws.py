"""WebSocket tests for /api/missions/ws (hello + replay + live fanout)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.missions.events import EventEnvelope, MissionDispatched
from jarvis.missions.manager import MissionManager
from jarvis.ui.web.missions_auth import (
    issue_token,
    reset_tokens,
    router as missions_auth_router,
)
from jarvis.ui.web.missions_routes import router as missions_router
from jarvis.ui.web.missions_ws_routes import (
    ConnectionManager,
    _drain_client_frames,
    router as missions_ws_router,
)


def _make_envelope(seq: int, mission_id: str = "m1") -> EventEnvelope:
    return EventEnvelope(
        seq=seq,
        mission_id=mission_id,
        source_actor="system",
        ts_ms=1,
        payload=MissionDispatched(prompt=f"p{seq}"),
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
                # Server should send close(4400)
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
    """Dispatch a mission BEFORE the WS connect → replay delivers it."""
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
    """``last_seq=1`` skips the first event and delivers only the second."""
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
    """Dispatch AFTER the connect → the event lands via fanout at the client."""
    with TestClient(app) as client:
        token = client.get("/api/missions/auth/token").json()["token"]

        with client.websocket_connect("/api/missions/ws") as ws:
            ws.send_json({"type": "hello", "last_seq": 0, "token": token})
            # Now trigger a mission → the live frame must arrive
            d = client.post(
                "/api/missions/dispatch", json={"prompt": "live!"}
            )
            assert d.status_code == 201
            mid = d.json()["mission_id"]
            frame = ws.receive_json()
    assert frame["mission_id"] == mid
    assert frame["payload"]["event_type"] == "MissionDispatched"
    assert frame["payload"]["prompt"] == "live!"


# ---------------------------------------------------------------------------
# FIX 1 — AP-20: the client-frame reader must never spin on a dead socket
# ---------------------------------------------------------------------------


class _FakeWS:
    """Duck-types only the ``receive_json`` surface ``_drain_client_frames`` uses."""

    def __init__(self, effects: list) -> None:
        self._effects = list(effects)
        self.call_count = 0

    async def receive_json(self):
        self.call_count += 1
        effect = self._effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


@pytest.mark.asyncio
async def test_reader_terminates_on_runtime_error_no_spin() -> None:
    """A RuntimeError (unclean disconnect) must return, not `continue` forever."""
    ws = _FakeWS([RuntimeError("WebSocket is not connected. Need to call accept.")])
    await asyncio.wait_for(_drain_client_frames(ws), timeout=1.0)
    assert ws.call_count == 1


@pytest.mark.asyncio
async def test_reader_survives_malformed_frame_then_exits_on_disconnect() -> None:
    """A malformed JSON frame (ValueError) is skipped; a clean disconnect exits."""
    ws = _FakeWS([ValueError("Expecting value: line 1 column 1"), WebSocketDisconnect(1006)])
    await asyncio.wait_for(_drain_client_frames(ws), timeout=1.0)
    assert ws.call_count == 2


# ---------------------------------------------------------------------------
# FIX 2 — replay/registration race + drop-oldest gap notice
# ---------------------------------------------------------------------------


class _RacyStore:
    """Fires a live event via ``fanout()`` while ``events_since()`` is "in flight".

    Simulates the race the fix closes: a client already registered, but the
    replay SELECT hasn't returned yet, when a new event is published.
    """

    def __init__(
        self,
        conn_mgr: ConnectionManager,
        replay: list,
        live: EventEnvelope | None,
    ) -> None:
        self._conn_mgr = conn_mgr
        self._replay = replay
        self._live = live

    async def events_since(self, after_seq: int) -> list:
        if self._live is not None:
            await self._conn_mgr.fanout(self._live)
        return list(self._replay)


@pytest.mark.asyncio
async def test_connect_captures_event_published_during_replay_query() -> None:
    """An event fired while events_since() is in flight is not lost."""
    conn_mgr = ConnectionManager()
    replay = [_make_envelope(1), _make_envelope(2)]
    live = _make_envelope(3)
    store = _RacyStore(conn_mgr, replay, live)

    queue = await conn_mgr.connect("client-1", last_seq=0, store=store)

    received = []
    while not queue.empty():
        received.append(queue.get_nowait())
    assert [env.seq for env in received] == [1, 2, 3]


@pytest.mark.asyncio
async def test_connect_dedupes_event_already_covered_by_replay() -> None:
    """An event that both the SELECT and the live fanout captured is delivered once."""
    conn_mgr = ConnectionManager()
    env = _make_envelope(1)
    store = _RacyStore(conn_mgr, [env], env)

    queue = await conn_mgr.connect("client-1", last_seq=0, store=store)

    received = []
    while not queue.empty():
        received.append(queue.get_nowait())
    assert len(received) == 1
    assert received[0].seq == 1


@pytest.mark.asyncio
async def test_fanout_overflow_emits_one_gap_frame() -> None:
    """A drop-oldest overflow arms exactly one {"type": "gap"} frame per client.

    maxsize=3: seq 1..3 fill the queue exactly; seq 4 is the single overflow
    that drops seq 1 and arms the gap notice (which itself then evicts seq 2).
    """
    conn_mgr = ConnectionManager()
    queue: asyncio.Queue = asyncio.Queue(maxsize=3)
    conn_mgr._clients["client-1"] = queue  # noqa: SLF001 - test-only direct wiring

    for seq in range(1, 5):
        await conn_mgr.fanout(_make_envelope(seq))

    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    gap_frames = [item for item in items if isinstance(item, dict) and item.get("type") == "gap"]
    assert len(gap_frames) == 1
    real_seqs = [item.seq for item in items if isinstance(item, EventEnvelope)]
    assert real_seqs == [3, 4]
