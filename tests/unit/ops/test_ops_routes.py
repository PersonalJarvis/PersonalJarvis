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


async def test_only_the_priority_marks_are_writable() -> None:
    """The ledger and agenda only read; the person's marks are the one write."""
    for route in ops_routes.router.routes:
        methods = set(getattr(route, "methods", ()))
        path = getattr(route, "path", "")
        if methods - {"GET", "HEAD"}:
            assert path.startswith("/api/ops/priorities/"), path
        else:
            assert methods <= {"GET", "HEAD"}


# --- Priorities and agenda ------------------------------------------------------


async def _send(app: FastAPI, method: str, url: str, **kw: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.request(method, url, **kw)


@pytest.fixture
def marks_app(app: FastAPI, tmp_path: Path) -> FastAPI:
    from jarvis.ops.priority import OpsPriorityStore

    app.state.ops_priority_store = OpsPriorityStore(tmp_path / "ops.sqlite")
    return app


def _task_rows(db: Path) -> list[tuple[Any, ...]]:
    import sqlite3

    con = sqlite3.connect(db)
    try:
        return sorted(map(tuple, con.execute("SELECT * FROM tasks")))
    finally:
        con.close()


async def test_focus_mark_ranks_the_item_and_leaves_the_task_untouched(
    marks_app: FastAPI, tmp_path: Path
) -> None:
    item = (await _get(marks_app, "/api/ops/work")).json()["items"][0]
    before = _task_rows(tmp_path / "tasks.db")

    put = await _send(
        marks_app,
        "PUT",
        f"/api/ops/priorities/task/{item['id']}",
        json={"priority": "high", "focus_today": True, "note": "before the call"},
    )
    assert put.status_code == 200
    assert put.json()["focus_today"] is True and put.json()["priority"] == "high"
    assert _task_rows(tmp_path / "tasks.db") == before  # the task itself is unchanged

    agenda = (await _get(marks_app, "/api/ops/agenda")).json()
    active = agenda["lanes"]["active"]
    assert [(r["id"], r["priority"], r["focus_today"]) for r in active] == [
        (item["id"], "high", True)
    ]
    assert active[0]["reasons"][:2] == ["focus_today", "priority:high"]
    assert [r["id"] for r in agenda["focus"]] == [item["id"]]
    assert agenda["needs_you"] == []

    listed = (await _get(marks_app, "/api/ops/priorities")).json()["priorities"]
    assert [(p["item_id"], p["note"]) for p in listed] == [(item["id"], "before the call")]

    removed = await _send(marks_app, "DELETE", f"/api/ops/priorities/task/{item['id']}")
    assert removed.json()["removed"] is True
    after = (await _get(marks_app, "/api/ops/agenda")).json()["lanes"]["active"][0]
    assert (after["priority"], after["reasons"][0]) == (None, "no_priority")


@pytest.mark.parametrize(
    "url,body",
    [
        ("/api/ops/priorities/calendar/x", {"priority": "high"}),
        ("/api/ops/priorities/task/x", {"priority": "critical"}),
        ("/api/ops/priorities/task/x", {"priority": "high", "extra": 1}),
    ],
)
async def test_invalid_marks_are_rejected(marks_app: FastAPI, url: str, body: dict) -> None:
    res = await _send(marks_app, "PUT", url, json=body)
    assert res.status_code in (400, 422)
    assert (await _get(marks_app, "/api/ops/priorities")).json()["priorities"] == []


async def test_the_marks_store_opens_lazily_under_the_data_dir(
    app: FastAPI, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    data_dir = tmp_path / "data"
    app.state.config = SimpleNamespace(memory=SimpleNamespace(data_dir=str(data_dir)))
    assert not (data_dir / "ops.sqlite").exists()
    res = await _send(app, "PUT", "/api/ops/priorities/task/t1", json={"priority": "low"})
    assert res.status_code == 200
    assert (data_dir / "ops.sqlite").exists()
