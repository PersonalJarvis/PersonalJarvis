"""Integration-Tests fuer die Task-Queue-REST-API (Phase 5).

Pattern: FastAPI ``TestClient`` + in-memory TaskStore (via ``tmp_path``).
POST → GET → POST-cancel → GET-state-cancelled.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.scheduler import TaskScheduler
from jarvis.tasks.store import TaskStore
from jarvis.ui.web.server import WebServer

pytestmark = pytest.mark.phase5


@pytest.fixture
async def wired_server(tmp_path: Path):
    """WebServer mit TaskStore + Scheduler (ohne Runner-Dispatch)."""
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    bus = EventBus()
    server = WebServer(cfg, bus=bus)

    store = TaskStore(tmp_path / "tasks.db")
    await store.init()
    runner = TaskRunner(store=store, bus=bus)  # kein TTS/HM — laeuft nie
    scheduler = TaskScheduler(store=store, bus=bus, runner=runner)

    server.app.state.task_store = store
    server.app.state.task_scheduler = scheduler

    try:
        yield server
    finally:
        await store.close()


def _make_spec_payload(title: str = "Erinner mich", delay: float = 3600.0) -> dict:
    return {
        "title": title,
        "trigger": {"type": "after_delay", "delay_seconds": delay},
        "action": {"kind": "speak", "text": "Zeit ist um!"},
    }


def test_post_task_returns_id(wired_server) -> None:
    with TestClient(wired_server.app) as client:
        resp = client.post("/api/tasks", json=_make_spec_payload())
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert len(body["id"]) > 10


def test_full_lifecycle_post_get_cancel_get(wired_server) -> None:
    with TestClient(wired_server.app) as client:
        # POST
        resp = client.post("/api/tasks", json=_make_spec_payload("Lifecycle"))
        assert resp.status_code == 201
        tid = resp.json()["id"]

        # GET list
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert any(t["id"] == tid for t in tasks)
        task_row = next(t for t in tasks if t["id"] == tid)
        assert task_row["state"] == "scheduled"
        assert task_row["title"] == "Lifecycle"

        # GET detail
        resp = client.get(f"/api/tasks/{tid}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["id"] == tid
        assert detail["spec"] is not None
        assert detail["spec"]["title"] == "Lifecycle"
        assert detail["steps"] == []

        # POST cancel
        resp = client.post(f"/api/tasks/{tid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["state"] == "cancelled"

        # GET after cancel
        resp = client.get(f"/api/tasks/{tid}")
        assert resp.status_code == 200
        assert resp.json()["state"] == "cancelled"


def test_get_task_404_when_missing(wired_server) -> None:
    with TestClient(wired_server.app) as client:
        resp = client.get("/api/tasks/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


def test_delete_blocked_while_scheduled(wired_server) -> None:
    with TestClient(wired_server.app) as client:
        resp = client.post("/api/tasks", json=_make_spec_payload("will-block"))
        tid = resp.json()["id"]

        resp = client.delete(f"/api/tasks/{tid}")
        # 409 weil state=scheduled
        assert resp.status_code == 409


def test_delete_after_cancel_ok(wired_server) -> None:
    with TestClient(wired_server.app) as client:
        resp = client.post("/api/tasks", json=_make_spec_payload("delete-me"))
        tid = resp.json()["id"]

        client.post(f"/api/tasks/{tid}/cancel")

        resp = client.delete(f"/api/tasks/{tid}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        resp = client.get(f"/api/tasks/{tid}")
        assert resp.status_code == 404


def test_list_with_state_filter(wired_server) -> None:
    with TestClient(wired_server.app) as client:
        # Zwei Tasks, einen cancellen
        tid1 = client.post("/api/tasks", json=_make_spec_payload("A")).json()["id"]
        tid2 = client.post("/api/tasks", json=_make_spec_payload("B")).json()["id"]
        client.post(f"/api/tasks/{tid1}/cancel")

        resp = client.get("/api/tasks?state=scheduled")
        assert resp.status_code == 200
        ids = {t["id"] for t in resp.json()["tasks"]}
        assert tid2 in ids
        assert tid1 not in ids

        resp = client.get("/api/tasks?state=cancelled")
        ids = {t["id"] for t in resp.json()["tasks"]}
        assert tid1 in ids
        assert tid2 not in ids


def test_post_rejects_invalid_spec(wired_server) -> None:
    with TestClient(wired_server.app) as client:
        # delay_seconds <= 0 ist von Pydantic geblockt (gt=0)
        resp = client.post("/api/tasks", json={
            "title": "bad",
            "trigger": {"type": "after_delay", "delay_seconds": 0.0},
            "action": {"kind": "speak", "text": "x"},
        })
        assert resp.status_code == 422


def test_service_unavailable_when_store_missing(tmp_path: Path) -> None:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    bus = EventBus()
    server = WebServer(cfg, bus=bus)
    # Bewusst kein app.state.task_store setzen
    with TestClient(server.app) as client:
        resp = client.get("/api/tasks")
        assert resp.status_code == 503
