"""Routine edits share the creation route's per-turn timezone contract."""
from __future__ import annotations

from contextvars import ContextVar

import httpx
import pytest
from fastapi import FastAPI

from jarvis.core.bus import EventBus
from jarvis.society.runtime import SocietyRuntime
from jarvis.tasks import context
from jarvis.tasks.scheduler import TaskScheduler
from jarvis.tasks.store import TaskStore
from jarvis.ui.web.society_routes import router


@pytest.fixture
async def routine(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "_reported_ui_timezone", None)
    monkeypatch.setattr(
        context, "client_timezone", ContextVar("test_routine_client_timezone", default=None),
    )
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    store = TaskStore(tmp_path / "tasks.db")
    await store.init()
    scheduler = TaskScheduler(store, EventBus())
    app = FastAPI()
    app.include_router(router)
    app.state.society = None
    app.state.society_factory = lambda: runtime
    app.state.task_store = store
    app.state.task_scheduler = scheduler
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            created = await client.post("/api/society/agents", json={"name": "Scout"})
            assert created.status_code == 200, created.text
            saved = await client.post("/api/society/agents/scout/routines", json={
                "title": "Original", "prompt": "Summarize changes.",
                "schedule": {"kind": "every", "interval_seconds": 3600},
            })
            assert saved.status_code == 200, saved.text
            yield client, store, saved.json()["id"]
    finally:
        await scheduler.shutdown()
        await store.close()
        await runtime.close()


@pytest.mark.parametrize("schedule", [
    {"kind": "calendar", "local_time": "08:00"},
    {"kind": "cron", "expression": "0 8 * * *"},
    {"kind": "at_time", "iso_timestamp": "2099-10-05T08:00:00"},
])
async def test_update_requires_timezone_and_adopts_client_header(routine, schedule):
    client, store, task_id = routine
    path = f"/api/society/agents/scout/routines/{task_id}"
    body = {"title": "Updated", "prompt": "Summarize changes.", "schedule": schedule}
    before = await store.get_spec(task_id)

    rejected = await client.patch(path, json=body)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["reason"] == "timezone_required"
    assert await store.get_spec(task_id) == before

    updated = await client.patch(
        path, json=body, headers={context.CLIENT_TIMEZONE_HEADER: "Europe/Rome"},
    )
    if schedule["kind"] == "at_time":
        assert updated.status_code == 409, updated.text
        assert updated.json()["detail"] == "Routine updates require a recurring trigger"
        assert await store.get_spec(task_id) == before
        assert context.client_timezone.get() is None
        return
    assert updated.status_code == 200, updated.text
    stored = await store.get_spec(task_id)
    assert stored.id == before.id
    assert stored.trigger.type == schedule["kind"]
    assert stored.trigger.timezone == "Europe/Rome"
    assert context.client_timezone.get() is None


async def test_update_explicit_timezone_wins_and_invalid_header_leaves_spec_unchanged(routine):
    client, store, task_id = routine
    path = f"/api/society/agents/scout/routines/{task_id}"
    body = {"title": "Updated", "prompt": "Summarize changes.", "schedule": {
        "kind": "calendar", "local_time": "08:00", "timezone": "America/New_York",
    }}
    updated = await client.patch(
        path, json=body, headers={context.CLIENT_TIMEZONE_HEADER: "Europe/Rome"},
    )
    assert updated.status_code == 200, updated.text
    stored = await store.get_spec(task_id)
    assert stored.trigger.timezone == "America/New_York"

    body["schedule"].pop("timezone")
    rejected = await client.patch(
        path, json=body, headers={context.CLIENT_TIMEZONE_HEADER: "Invalid/Zone"},
    )
    assert rejected.status_code == 422, rejected.text
    assert await store.get_spec(task_id) == stored
    assert context.client_timezone.get() is None
