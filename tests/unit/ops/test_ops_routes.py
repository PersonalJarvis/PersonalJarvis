"""``GET /api/ops/work`` — read-only, never starts a source that is not up."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.society import runtime as society_runtime
from jarvis.tasks.schema import AgentAction, TaskSpec, TriggerEvery
from jarvis.tasks.store import TaskStore
from jarvis.ui.web import ops_routes


@pytest.fixture
async def app(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.db")
    await store.init()
    task_id = await store.insert(
        TaskSpec(
            title="Morning digest",
            trigger=TriggerEvery(interval_seconds=86400),
            action=AgentAction(prompt="Summarise my day."),
        )
    )
    await store.update_state(task_id, "scheduled")
    application = FastAPI()
    application.include_router(ops_routes.router)
    application.state.task_store = store
    application.state.society = None  # not built yet
    try:
        yield application
    finally:
        await store.close()


async def _get(app: FastAPI, url: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(url)


async def test_lists_work_and_reports_missing_sources(app: FastAPI) -> None:
    res = await _get(app, "/api/ops/work")
    assert res.status_code == 200
    body: dict[str, Any] = res.json()
    assert [(i["source"], i["title"], i["status"]) for i in body["items"]] == [
        ("task", "Morning digest", "scheduled")
    ]
    assert {s["source"]: s["state"] for s in body["sources"]} == {
        "mission": "unavailable",
        "task": "ok",
        "quest": "unavailable",
        "workflow": "unavailable",
    }
    # Reading never builds the society runtime.
    assert app.state.society is None
    assert society_runtime.current_runtime() is None


async def test_source_filter_and_unknown_source(app: FastAPI) -> None:
    res = await _get(app, "/api/ops/work?source=task&include_finished=false")
    assert res.status_code == 200
    assert [s["source"] for s in res.json()["sources"]] == ["task"]

    bad = await _get(app, "/api/ops/work?source=task,calendar")
    assert bad.status_code == 400


async def test_the_router_has_no_write_route() -> None:
    methods = {m for route in ops_routes.router.routes for m in getattr(route, "methods", ())}
    assert methods <= {"GET", "HEAD"}

