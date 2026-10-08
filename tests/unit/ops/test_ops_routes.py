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
    """The ledger and agenda only read. Writes: the person's marks and the
    notification opt-in. The briefing preview writes nothing; the notify
    simulation only records its own delivery log (both proven below)."""
    for route in ops_routes.router.routes:
        methods = set(getattr(route, "methods", ()))
        path = getattr(route, "path", "")
        if path in ("/api/ops/briefing/preview", "/api/ops/notify/simulate"):
            assert methods == {"POST"}
        elif path == "/api/ops/notify/settings":
            assert methods <= {"GET", "HEAD", "PUT"}  # the person's opt-in
        elif methods - {"GET", "HEAD"}:
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


# --- Briefing preview -------------------------------------------------------------


async def test_briefing_preview_sends_nothing_and_writes_nothing(
    marks_app: FastAPI, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    item = (await _get(marks_app, "/api/ops/work")).json()["items"][0]
    await _send(
        marks_app, "PUT", f"/api/ops/priorities/task/{item['id']}", json={"focus_today": True}
    )
    marks_app.state.config = SimpleNamespace(ui=SimpleNamespace(language="de"))
    tasks_before = _task_rows(tmp_path / "tasks.db")
    marks_before = (await _get(marks_app, "/api/ops/priorities")).json()

    res = await _send(marks_app, "POST", "/api/ops/briefing/preview", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["language"] == "de"  # the UI language by default
    focus = next(s for s in body["sections"] if s["key"] == "focus")
    assert [i["id"] for i in focus["items"]] == [item["id"]]
    assert next(s for s in body["sections"] if s["key"] == "calendar")["status"] == (
        "not_connected"
    )
    assert body["phrasing"] == {"status": "not_requested", "reason": "", "text": None}
    assert "Morning digest" in body["text"]

    assert _task_rows(tmp_path / "tasks.db") == tasks_before
    assert (await _get(marks_app, "/api/ops/priorities")).json() == marks_before


async def test_briefing_preview_phrasing_without_a_subscription_is_refused(
    marks_app: FastAPI,
) -> None:
    from types import SimpleNamespace

    calls: list[Any] = []

    class _Brain:
        async def run_task(self, **kw: Any) -> str:
            calls.append(kw)
            return "prose"

    marks_app.state.brain = _Brain()
    worker = SimpleNamespace(provider="", model="", reasoning_effort="")
    marks_app.state.config = SimpleNamespace(
        ui=SimpleNamespace(language="en"), brain=SimpleNamespace(worker=worker)
    )
    res = await _send(
        marks_app, "POST", "/api/ops/briefing/preview", json={"phrase": True, "language": "es"}
    )
    body = res.json()
    assert body["language"] == "es"
    assert body["phrasing"]["status"] == "not_allowed"
    assert body["text"]  # the deterministic briefing is always there
    assert calls == []


async def test_briefing_preview_rejects_unknown_fields(marks_app: FastAPI) -> None:
    res = await _send(marks_app, "POST", "/api/ops/briefing/preview", json={"send": True})
    assert res.status_code == 422


# --- Notifications (simulated Telegram only) -------------------------------------


@pytest.fixture
def notify_app(marks_app: FastAPI, tmp_path: Path) -> FastAPI:
    from jarvis.ops.notify import NotifyStore

    marks_app.state.ops_notify_store = NotifyStore(tmp_path / "ops.sqlite")
    return marks_app


async def test_notifications_are_off_until_the_person_opts_in(notify_app: FastAPI) -> None:
    settings = (await _get(notify_app, "/api/ops/notify/settings")).json()
    assert settings["enabled"] is False and settings["transport"] == "simulated"

    off = (await _send(notify_app, "POST", "/api/ops/notify/simulate", json={})).json()
    assert off["simulated_messages"] == []
    assert set(off["counts"]) == {"disabled"}
    assert (await _get(notify_app, "/api/ops/notify/outbox")).json()["items"] == []


async def test_opt_in_simulates_each_notification_once(notify_app: FastAPI) -> None:
    put = await _send(notify_app, "PUT", "/api/ops/notify/settings", json={"enabled": True})
    assert put.status_code == 200 and put.json()["enabled"] is True

    first = (await _send(notify_app, "POST", "/api/ops/notify/simulate", json={})).json()
    assert first["counts"] == {"simulated": 1}  # the daily briefing; nothing else is due
    assert first["simulated_messages"][0].startswith("Briefing for ")

    second = (await _send(notify_app, "POST", "/api/ops/notify/simulate", json={})).json()
    assert second["counts"] == {"duplicate": 1} and second["simulated_messages"] == []

    outbox = (await _get(notify_app, "/api/ops/notify/outbox")).json()["items"]
    assert [(i["kind"], i["status"], i["transport"]) for i in outbox] == [
        ("daily_briefing", "simulated", "telegram-simulated")
    ]


async def test_notify_settings_are_validated(notify_app: FastAPI) -> None:
    bad = await _send(
        notify_app, "PUT", "/api/ops/notify/settings", json={"enabled": True, "kinds": ["sms"]}
    )
    assert bad.status_code == 400
    extra = await _send(
        notify_app, "PUT", "/api/ops/notify/settings", json={"enabled": True, "chat_id": 1}
    )
    assert extra.status_code == 422  # no chat id or token is ever accepted here
    assert (await _get(notify_app, "/api/ops/notify/settings")).json()["enabled"] is False
