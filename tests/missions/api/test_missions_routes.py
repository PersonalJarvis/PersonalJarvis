"""REST-Route-Tests fuer das Phase-6 Mission-API.

Pattern: FastAPI ``TestClient`` + frischer ``MissionManager`` pro Test
(via ``tmp_path``-Fixture). Deckt:
- 503 wenn ``app.state.mission_manager`` nicht gesetzt ist.
- Listing leer / mit Eintraegen / mit State-Filter.
- Detail mit Events + Verdicts.
- Dispatch ohne Kontrollierer (Mission landet in PENDING, ``started=false``).
- Dispatch mit Stub-Kontrollierer (BackgroundTask wird scheduled).
- Cancel happy + 404 + 409 (terminal-state).
- Kill 503 ohne Kontrollierer.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.missions.events import (
    CriticVerdictReady,
    EventEnvelope,
    WorkerKilled,
    WorkerSpawned,
    now_ms,
)
from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import MissionState
from jarvis.ui.web.missions_routes import router as missions_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def manager(tmp_path: Path):
    """Frischer MissionManager mit eigenem tmp-DB-Path."""
    mgr = MissionManager(tmp_path / "missions.db")
    await mgr.start()
    try:
        yield mgr
    finally:
        await mgr.stop()


@pytest.fixture
def app_no_manager() -> FastAPI:
    """FastAPI ohne mission_manager — fuer 503-Pfad."""
    app = FastAPI()
    app.include_router(missions_router)
    return app


@pytest.fixture
def app_with_manager(manager: MissionManager) -> FastAPI:
    app = FastAPI()
    app.include_router(missions_router)
    app.state.mission_manager = manager
    return app


# ---------------------------------------------------------------------------
# 503 ohne Manager
# ---------------------------------------------------------------------------


def test_list_returns_503_without_manager(app_no_manager: FastAPI) -> None:
    with TestClient(app_no_manager) as client:
        r = client.get("/api/missions")
    assert r.status_code == 503
    assert "MissionManager" in r.json()["detail"]


def test_get_returns_503_without_manager(app_no_manager: FastAPI) -> None:
    with TestClient(app_no_manager) as client:
        r = client.get("/api/missions/some-id")
    assert r.status_code == 503


def test_dispatch_returns_503_without_manager(app_no_manager: FastAPI) -> None:
    with TestClient(app_no_manager) as client:
        r = client.post("/api/missions/dispatch", json={"prompt": "hi"})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_list_empty(app_with_manager: FastAPI) -> None:
    with TestClient(app_with_manager) as client:
        r = client.get("/api/missions")
    assert r.status_code == 200
    body = r.json()
    assert body == {"missions": [], "total": 0}


def test_dispatch_then_list_contains_mission(
    app_with_manager: FastAPI,
) -> None:
    with TestClient(app_with_manager) as client:
        d = client.post(
            "/api/missions/dispatch",
            json={"prompt": "Phase-6-Test", "language": "de"},
        )
        assert d.status_code == 201
        mission_id = d.json()["mission_id"]
        # Ohne Kontrollierer wird nicht gestartet
        assert d.json()["started"] == "false"

        r = client.get("/api/missions")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        m = body["missions"][0]
        assert m["id"] == mission_id
        assert m["prompt"] == "Phase-6-Test"
        assert m["state"] == MissionState.PENDING.value
        assert m["language"] == "de"


def test_list_state_filter_excludes_unmatched(
    app_with_manager: FastAPI,
) -> None:
    with TestClient(app_with_manager) as client:
        d = client.post("/api/missions/dispatch", json={"prompt": "x"})
        mid = d.json()["mission_id"]

        # Filter PENDING enthaelt unsere Mission
        r1 = client.get("/api/missions?state=PENDING")
        assert r1.status_code == 200
        ids = [m["id"] for m in r1.json()["missions"]]
        assert mid in ids

        # Filter APPROVED enthaelt sie nicht
        r2 = client.get("/api/missions?state=APPROVED")
        assert r2.status_code == 200
        assert r2.json()["total"] == 0


def test_list_rejects_unknown_state(app_with_manager: FastAPI) -> None:
    with TestClient(app_with_manager) as client:
        r = client.get("/api/missions?state=BOGUS")
    assert r.status_code == 400
    assert "BOGUS" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def test_get_returns_404_for_unknown(app_with_manager: FastAPI) -> None:
    with TestClient(app_with_manager) as client:
        r = client.get("/api/missions/does-not-exist")
    assert r.status_code == 404


async def test_get_returns_events_and_verdicts(
    manager: MissionManager,
) -> None:
    """Erzeugt eine Mission, hangt manuell einen CriticVerdictReady-Event an
    und prueft dass /api/missions/{id} ihn unter ``verdicts`` listet."""
    mid = await manager.dispatch(prompt="critic test")

    env = EventEnvelope(
        mission_id=mid,
        worker_id="worker-1",
        source_actor="critic",
        ts_ms=now_ms(),
        payload=CriticVerdictReady(
            worker_id="worker-1",
            verdict="approve",
            summary="ok",
            confidence=0.9,
            axes={},
            iteration=0,
        ),
    )
    await manager.store.append_and_publish(env)

    app = FastAPI()
    app.include_router(missions_router)
    app.state.mission_manager = manager
    with TestClient(app) as client:
        r = client.get(f"/api/missions/{mid}")
    assert r.status_code == 200
    body = r.json()
    assert body["mission"]["id"] == mid
    assert body["mission"]["state"] == MissionState.PENDING.value
    # 2 Events: MissionDispatched + CriticVerdictReady
    assert len(body["events"]) == 2
    # 1 Verdict
    assert len(body["verdicts"]) == 1
    assert body["verdicts"][0]["verdict"] == "approve"


# ---------------------------------------------------------------------------
# Dispatch + Background-Task
# ---------------------------------------------------------------------------


def test_dispatch_with_kontrollierer_schedules_background_task(
    app_with_manager: FastAPI,
) -> None:
    calls: list[str] = []

    class StubKontrollierer:
        async def run_mission(self, mission_id: str) -> None:
            calls.append(mission_id)

    app_with_manager.state.kontrollierer = StubKontrollierer()
    with TestClient(app_with_manager) as client:
        r = client.post("/api/missions/dispatch", json={"prompt": "go"})
    assert r.status_code == 201
    body = r.json()
    assert body["started"] == "true"
    # BackgroundTask laeuft NACH dem Response — TestClient awaitet das.
    assert calls == [body["mission_id"]]


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def test_cancel_pending_mission_succeeds(
    app_with_manager: FastAPI,
) -> None:
    with TestClient(app_with_manager) as client:
        d = client.post("/api/missions/dispatch", json={"prompt": "abort me"})
        mid = d.json()["mission_id"]

        c = client.post(f"/api/missions/{mid}/cancel")
        assert c.status_code == 200
        body = c.json()
        assert body["ok"] is True
        assert body["state"] == MissionState.CANCELLED.value

        g = client.get(f"/api/missions/{mid}")
        assert g.json()["mission"]["state"] == MissionState.CANCELLED.value


def test_cancel_unknown_returns_404(app_with_manager: FastAPI) -> None:
    with TestClient(app_with_manager) as client:
        r = client.post("/api/missions/no-such-id/cancel")
    assert r.status_code == 404


def test_cancel_already_cancelled_returns_409(
    app_with_manager: FastAPI,
) -> None:
    with TestClient(app_with_manager) as client:
        d = client.post("/api/missions/dispatch", json={"prompt": "x"})
        mid = d.json()["mission_id"]
        c1 = client.post(f"/api/missions/{mid}/cancel")
        assert c1.status_code == 200
        c2 = client.post(f"/api/missions/{mid}/cancel")
    assert c2.status_code == 409


# ---------------------------------------------------------------------------
# Cancel kills the in-flight run (UI hold-to-abort feature)
# ---------------------------------------------------------------------------


class _CancelStubKontrollierer:
    """``run_mission`` no-op + records ``cancel_running_mission`` calls."""

    def __init__(self, *, cancel_result: bool = True) -> None:
        self.cancel_calls: list[str] = []
        self._cancel_result = cancel_result

    async def run_mission(self, mission_id: str) -> None:
        return None

    def cancel_running_mission(self, mission_id: str) -> bool:
        self.cancel_calls.append(mission_id)
        return self._cancel_result


def test_cancel_invokes_kontrollierer_task_kill(
    app_with_manager: FastAPI,
) -> None:
    """POST /cancel must also kill the in-flight run_mission task —
    flipping the DB state alone leaves the worker burning tokens."""
    stub = _CancelStubKontrollierer(cancel_result=True)
    app_with_manager.state.kontrollierer = stub
    with TestClient(app_with_manager) as client:
        d = client.post("/api/missions/dispatch", json={"prompt": "abort me"})
        mid = d.json()["mission_id"]
        c = client.post(f"/api/missions/{mid}/cancel")
    assert c.status_code == 200
    body = c.json()
    assert body["worker_killed"] is True
    assert stub.cancel_calls == [mid]


def test_cancel_reports_worker_killed_false_without_kontrollierer(
    app_with_manager: FastAPI,
) -> None:
    with TestClient(app_with_manager) as client:
        d = client.post("/api/missions/dispatch", json={"prompt": "x"})
        mid = d.json()["mission_id"]
        c = client.post(f"/api/missions/{mid}/cancel")
    assert c.status_code == 200
    assert c.json()["worker_killed"] is False


def test_cancel_appends_mission_cancelled_event(
    app_with_manager: FastAPI,
) -> None:
    """Cancel writes the canonical ``MissionCancelled`` terminal event —
    recovery reconciliation and the voice announcer both key off it."""
    with TestClient(app_with_manager) as client:
        d = client.post("/api/missions/dispatch", json={"prompt": "x"})
        mid = d.json()["mission_id"]
        c = client.post(f"/api/missions/{mid}/cancel")
        assert c.status_code == 200
        g = client.get(f"/api/missions/{mid}")
    types = [e["payload"]["event_type"] for e in g.json()["events"]]
    assert "MissionCancelled" in types


# ---------------------------------------------------------------------------
# Kill
# ---------------------------------------------------------------------------


def test_kill_returns_503_without_kontrollierer(
    app_with_manager: FastAPI,
) -> None:
    with TestClient(app_with_manager) as client:
        r = client.post("/api/missions/kill/worker-xyz")
    assert r.status_code == 503


def test_kill_invokes_kontrollierer_method_when_present(
    app_with_manager: FastAPI,
) -> None:
    killed: list[str] = []

    class StubKontrollierer:
        async def kill_worker(self, worker_id: str) -> bool:
            killed.append(worker_id)
            return True

    app_with_manager.state.kontrollierer = StubKontrollierer()
    with TestClient(app_with_manager) as client:
        r = client.post("/api/missions/kill/abc-123")
    assert r.status_code == 200
    assert r.json()["killed"] is True
    assert killed == ["abc-123"]


# ---------------------------------------------------------------------------
# Phase 9 (Welle 4 UI) — OpenClaw-Worker-Snapshots im Detail-Endpoint
# ---------------------------------------------------------------------------


async def test_get_returns_empty_openclaw_workers_when_no_openclaw_marker(
    manager: MissionManager,
) -> None:
    """Ohne step.harness=='openclaw' Marker: ``openclaw_workers`` ist [] aber
    immer im Response (Frontend kann sich darauf verlassen)."""
    mid = await manager.dispatch(prompt="non-openclaw mission")

    spawn_env = EventEnvelope(
        mission_id=mid,
        worker_id="claude-worker",
        source_actor="kontrollierer",
        ts_ms=now_ms(),
        payload=WorkerSpawned(
            worker_id="claude-worker",
            step={"task": "build x"},
            pid=1234,
            cli="claude",
            model="claude-sonnet-4-6",
            worktree="C:/wt/agent-1",
            session_id=None,
        ),
    )
    await manager.store.append_and_publish(spawn_env)

    app = FastAPI()
    app.include_router(missions_router)
    app.state.mission_manager = manager
    with TestClient(app) as client:
        r = client.get(f"/api/missions/{mid}")
    assert r.status_code == 200
    body = r.json()
    assert "openclaw_workers" in body
    assert body["openclaw_workers"] == []


async def test_get_returns_openclaw_worker_snapshot(
    manager: MissionManager,
) -> None:
    """Mit step.harness=='openclaw' + WorkerKilled: alle Spalten gefuellt."""
    mid = await manager.dispatch(prompt="openclaw mission")

    spawn_env = EventEnvelope(
        mission_id=mid,
        worker_id="oc-worker-1",
        source_actor="kontrollierer",
        ts_ms=1000,
        payload=WorkerSpawned(
            worker_id="oc-worker-1",
            step={"harness": "openclaw"},
            pid=4242,
            cli="python",
            model="gemini/gemini-3.1-pro-preview",
            worktree="C:/wt/oc-1",
            session_id="sess-cafebabe",
        ),
    )
    await manager.store.append_and_publish(spawn_env)

    kill_env = EventEnvelope(
        mission_id=mid,
        worker_id="oc-worker-1",
        source_actor="ui",
        ts_ms=2000,
        payload=WorkerKilled(worker_id="oc-worker-1", reason="user"),
    )
    await manager.store.append_and_publish(kill_env)

    app = FastAPI()
    app.include_router(missions_router)
    app.state.mission_manager = manager
    with TestClient(app) as client:
        r = client.get(f"/api/missions/{mid}")
    assert r.status_code == 200
    body = r.json()
    workers = body["openclaw_workers"]
    assert len(workers) == 1
    w = workers[0]
    assert w["worker_id"] == "oc-worker-1"
    assert w["model"] == "gemini/gemini-3.1-pro-preview"
    assert w["session_id"] == "sess-cafebabe"
    assert w["state_dir"] == "C:/wt/oc-1/.openclaw_state/sess-cafebabe/openclaw_state"
    assert w["log_path"].endswith("/run.log")
    assert w["reattach_status"] == "killed"
    assert w["ended_reason"] == "user"
    assert w["pid"] == 4242
