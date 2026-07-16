"""Run-control REST surface for Computer-Use goals (deep-dive H-09).

Pins the CLI-first contract for the new surface: an honest 503 when Computer
Use is not wired, start hands back a pollable mission id, duplicate active
goals are absorbed with 409, and cancel is per-id with honest 404/409.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.control import CancelToken
from jarvis.harness import cu_run_registry as reg
from jarvis.ui.web import computer_use_routes as cu_routes
from jarvis.ui.web.computer_use_routes import router as computer_use_router


@pytest.fixture(autouse=True)
def _clean_registry():
    reg.clear_runs()
    yield
    reg.clear_runs()


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(computer_use_router)
    return app


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    """Pretend the CU context is wired and capture launches (no desktop)."""
    from jarvis.harness import computer_use_context as ctx_mod

    monkeypatch.setattr(
        ctx_mod, "peek_computer_use_context", lambda: object(),
    )
    launches: list[tuple[str, str, float]] = []
    monkeypatch.setattr(
        cu_routes,
        "_launch_mission",
        lambda mission_id, goal, timeout_s: launches.append(
            (mission_id, goal, timeout_s)
        ),
    )
    return launches


# ----------------------------------------------------------------------
# Start
# ----------------------------------------------------------------------

def test_start_refuses_honestly_when_cu_is_not_wired(app: FastAPI) -> None:
    with TestClient(app) as client:
        r = client.post("/api/computer-use/goals", json={"goal": "open the browser"})
    assert r.status_code == 503
    assert "computer_use.enabled" in r.json()["detail"]


def test_start_returns_a_pollable_mission_id(app: FastAPI, wired: list) -> None:
    with TestClient(app) as client:
        r = client.post("/api/computer-use/goals", json={"goal": "open the browser"})
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "queued"
        mission_id = body["mission_id"]
        assert mission_id

        # Immediately visible to GET — no race window for the caller.
        got = client.get(f"/api/computer-use/goals/{mission_id}")
    assert got.status_code == 200
    assert got.json()["goal"] == "open the browser"
    assert got.json()["source"] == "api"
    assert wired == [(mission_id, "open the browser", 120.0)]


def test_start_absorbs_a_duplicate_active_goal(app: FastAPI, wired: list) -> None:
    with TestClient(app) as client:
        first = client.post(
            "/api/computer-use/goals", json={"goal": "Open  The Browser"}
        )
        dup = client.post(
            "/api/computer-use/goals", json={"goal": "open the browser"}
        )
    assert first.status_code == 201
    assert dup.status_code == 409
    assert dup.json()["detail"]["mission_id"] == first.json()["mission_id"]
    assert len(wired) == 1  # the duplicate never launched


def test_start_validates_goal_and_timeout(app: FastAPI, wired: list) -> None:
    with TestClient(app) as client:
        blank = client.post("/api/computer-use/goals", json={"goal": "   "})
        bad_timeout = client.post(
            "/api/computer-use/goals",
            json={"goal": "x", "timeout_s": 999999},
        )
    assert blank.status_code == 400
    assert bad_timeout.status_code == 422  # pydantic bound
    assert wired == []


# ----------------------------------------------------------------------
# List / get
# ----------------------------------------------------------------------

def _seed(mission_id: str, goal: str, *, status: str = "queued") -> CancelToken:
    token = CancelToken()
    reg.register_run(mission_id, goal, token, source="voice")
    if status == "running":
        reg.mark_running(mission_id)
    elif status != "queued":
        reg.finish_run(mission_id, status, exit_code=0)
    return token


def test_list_shows_runs_from_every_launch_route(app: FastAPI) -> None:
    _seed("v1", "voice goal", status="running")
    _seed("t1", "old goal", status="finished")
    with TestClient(app) as client:
        r = client.get("/api/computer-use/goals")
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == 1
    assert {run["mission_id"] for run in body["runs"]} == {"v1", "t1"}
    assert all("token" not in run for run in body["runs"])


def test_get_unknown_id_is_404(app: FastAPI) -> None:
    with TestClient(app) as client:
        r = client.get("/api/computer-use/goals/nope")
    assert r.status_code == 404


# ----------------------------------------------------------------------
# Cancel
# ----------------------------------------------------------------------

def test_cancel_fires_the_run_token(app: FastAPI) -> None:
    token = _seed("r1", "goal", status="running")
    with TestClient(app) as client:
        r = client.post("/api/computer-use/goals/r1/cancel")
    assert r.status_code == 200
    assert r.json()["cancel_requested"] is True
    assert token.is_cancelled()
    assert token.reason == "api_cancel"


def test_cancel_is_honest_about_unknown_and_terminal(app: FastAPI) -> None:
    _seed("done1", "goal", status="finished")
    with TestClient(app) as client:
        unknown = client.post("/api/computer-use/goals/nope/cancel")
        terminal = client.post("/api/computer-use/goals/done1/cancel")
    assert unknown.status_code == 404
    assert terminal.status_code == 409


def test_cancel_all_hits_only_active_runs(app: FastAPI) -> None:
    t1 = _seed("a1", "goal one", status="running")
    t2 = _seed("a2", "goal two", status="queued")
    t3 = _seed("z1", "goal three", status="finished")
    with TestClient(app) as client:
        r = client.post("/api/computer-use/goals/cancel-all")
    assert r.status_code == 200
    assert r.json()["cancelled"] == 2
    assert t1.is_cancelled() and t2.is_cancelled()
    assert not t3.is_cancelled()


# ----------------------------------------------------------------------
# CLI-first contract
# ----------------------------------------------------------------------

def test_danger_flag_is_declared_on_start_and_cancel(app: FastAPI) -> None:
    """The dynamic CLI derives --yes gating from x-jarvis-dangerous."""
    with TestClient(app) as client:
        spec: dict[str, Any] = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert paths["/api/computer-use/goals"]["post"]["x-jarvis-dangerous"] is True
    assert (
        paths["/api/computer-use/goals/{mission_id}/cancel"]["post"][
            "x-jarvis-dangerous"
        ]
        is True
    )
    assert (
        paths["/api/computer-use/goals/cancel-all"]["post"]["x-jarvis-dangerous"]
        is True
    )
    # Every operation is tagged so `jarvis api computer-use` groups them.
    for item in paths.values():
        for op in item.values():
            assert op["tags"] == ["computer-use"]
