"""REST-route tests for the event-loop diagnostics endpoints.

The endpoint exists to NAME the owner of an AP-20-class cancellation
busy-loop (anyio ``_deliver_cancellation`` rescheduling forever because a
task inside the cancelled scope never finishes). The tests verify the two
signals the hunt relies on: a pending-cancel task surfaces with
``cancelling > 0`` and sorts to the top, and every snapshot carries a usable
await stack.

Helper tasks are spawned/torn down through tiny in-app routes because the
TestClient owns the event loop — test code never touches the loop directly.
Snapshots are taken first and asserted after cleanup so a failing assertion
cannot leak a pending task into loop teardown.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.diagnostics_routes import router


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def test_event_loop_tasks_reports_running_tasks():
    app = _app()

    async def _sleeper() -> None:
        await asyncio.sleep(30)

    @app.get("/spawn-sleeper")
    async def spawn_sleeper() -> dict[str, bool]:
        task = asyncio.ensure_future(_sleeper())
        task.set_name("diag-test-sleeper")
        app.state.sleeper = task
        return {"ok": True}

    @app.get("/kill-sleeper")
    async def kill_sleeper() -> dict[str, bool]:
        app.state.sleeper.cancel()
        try:
            await app.state.sleeper
        except asyncio.CancelledError:
            pass
        return {"ok": True}

    with TestClient(app) as client:
        assert client.get("/spawn-sleeper").status_code == 200
        body: dict[str, Any] = client.get("/api/diagnostics/event-loop-tasks").json()
        assert client.get("/kill-sleeper").status_code == 200

    assert body["total"] >= 1
    named = [t for t in body["tasks"] if t["name"] == "diag-test-sleeper"]
    assert named, "spawned task must appear in the snapshot"
    entry = named[0]
    assert entry["cancelling"] == 0
    assert entry["done"] is False
    assert any("_sleeper" in frame for frame in entry["stack"])


def test_cancel_refusing_task_sorts_first_with_cancelling_count():
    app = _app()
    stop = asyncio.Event()

    async def _refuses_cancel() -> None:
        # Deliberate AP-20 reproduction: swallow the cancel and keep looping —
        # exactly the shape that pins a CancelScope. The ``stop`` event is the
        # escape hatch the real bug lacks, so the TEST can always terminate it.
        while not stop.is_set():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                continue

    @app.get("/spawn-zombie")
    async def spawn_zombie() -> dict[str, bool]:
        task = asyncio.ensure_future(_refuses_cancel())
        task.set_name("diag-test-zombie")
        # Let the task take its first step into ``sleep`` before cancelling;
        # a cancel BEFORE the first step lands outside the try and kills it.
        await asyncio.sleep(0)
        task.cancel()
        app.state.zombie = task
        return {"ok": True}

    @app.get("/kill-zombie")
    async def kill_zombie() -> dict[str, bool]:
        stop.set()
        task = app.state.zombie
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return {"ok": True}

    with TestClient(app) as client:
        assert client.get("/spawn-zombie").status_code == 200
        body: dict[str, Any] = client.get("/api/diagnostics/event-loop-tasks").json()
        assert client.get("/kill-zombie").status_code == 200

    named = [t for t in body["tasks"] if t["name"] == "diag-test-zombie"]
    assert named, "zombie task must appear in the snapshot"
    assert named[0]["cancelling"] >= 1 or named[0]["must_cancel"]
    assert body["suspects"] >= 1
    # Suspects sort to the top of the list.
    assert body["tasks"][0]["cancelling"] >= 1 or body["tasks"][0]["must_cancel"]


def test_event_loop_lag_returns_measurements():
    app = _app()
    with TestClient(app) as client:
        body = client.get("/api/diagnostics/event-loop-lag").json()
    assert len(body["lags_ms"]) == 4
    assert body["max_ms"] >= 0.0
