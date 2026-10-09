"""``/api/ops/morning/settings`` — off by default, one task when on, validated."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.core.bus import EventBus
from jarvis.ops.morning import TASK_TAG_OVERVIEW, TASK_TAG_PREPARE, TASK_TAGS, MorningStore
from jarvis.ops.notify import NotifyStore
from jarvis.tasks.scheduler import TaskScheduler
from jarvis.tasks.store import TaskStore
from jarvis.ui.web import ops_routes


@pytest.fixture
async def app(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.db")
    await store.init()
    scheduler = TaskScheduler(store=store, bus=EventBus())
    application = FastAPI()
    application.include_router(ops_routes.router)
    application.state.task_store = store
    application.state.task_scheduler = scheduler
    application.state.config = SimpleNamespace(
        memory=SimpleNamespace(data_dir=str(tmp_path / "data")), ui=SimpleNamespace(language="de")
    )
    try:
        yield application
    finally:
        await scheduler.shutdown()
        await store.close()


async def _call(app: FastAPI, method: str, url: str, **kw: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.request(method, url, **kw)


async def _tagged(app: FastAPI, tags: tuple[str, ...] = TASK_TAGS) -> list[dict[str, Any]]:
    rows = await app.state.task_store.list(limit=100)
    return [r for r in rows if set(tags) & set(json.loads(r["spec_json"]).get("tags", []))]


async def test_off_by_default_with_no_task(app: FastAPI) -> None:
    body = (await _call(app, "GET", "/api/ops/morning/settings")).json()
    assert body["enabled"] is False and body["prepare_output"] == "none"
    assert body["overview_delivery"].startswith("telegram, simulated")
    assert body["notifications"] == {"enabled": False, "daily_briefing": True}
    assert await _tagged(app) == []


async def test_switching_on_needs_a_valid_timezone(app: FastAPI) -> None:
    missing = await _call(app, "PUT", "/api/ops/morning/settings", json={"enabled": True})
    assert missing.status_code == 400
    bad = await _call(
        app, "PUT", "/api/ops/morning/settings", json={"enabled": True, "timezone": "Mars/Base"}
    )
    assert bad.status_code == 400
    bad_time = await _call(
        app,
        "PUT",
        "/api/ops/morning/settings",
        json={"enabled": True, "timezone": "Europe/Madrid", "overview_time": "9am"},
    )
    assert bad_time.status_code == 422
    assert await _tagged(app) == []
    assert (await _call(app, "GET", "/api/ops/morning/settings")).json()["enabled"] is False


async def test_on_then_off_keeps_one_task_per_moment(app: FastAPI) -> None:
    on = await _call(
        app,
        "PUT",
        "/api/ops/morning/settings",
        json={"enabled": True, "timezone": "Europe/Madrid"},
    )
    assert on.status_code == 200
    body = on.json()
    assert (body["enabled"], body["language"]) == (True, "de")
    assert (body["prepare_time"], body["overview_time"]) == ("06:30", "07:00")
    assert body["prepare_output"] == "none"
    for tag in (TASK_TAG_PREPARE, TASK_TAG_OVERVIEW):
        [task] = await _tagged(app, (tag,))
        assert task["id"] == body["task_ids"][tag] and task["state"] == "scheduled"

    off = await _call(app, "PUT", "/api/ops/morning/settings", json={"enabled": False})
    assert off.json()["enabled"] is False and off.json()["timezone"] == "Europe/Madrid"
    assert [t["state"] for t in await _tagged(app)] == ["paused", "paused"]
    stored = await MorningStore(Path(app.state.config.memory.data_dir) / "ops.sqlite").settings()
    assert stored.enabled is False


async def test_switching_the_briefing_on_does_not_opt_in_to_notifications(app: FastAPI) -> None:
    await _call(app, "PUT", "/api/ops/morning/settings", json={"enabled": True, "timezone": "UTC"})
    notify = NotifyStore(Path(app.state.config.memory.data_dir) / "ops.sqlite")
    assert (await notify.settings()).enabled is False


async def test_no_scheduler_no_change(app: FastAPI) -> None:
    app.state.task_scheduler = None
    res = await _call(
        app, "PUT", "/api/ops/morning/settings", json={"enabled": True, "timezone": "UTC"}
    )
    assert res.status_code == 503
    assert (await _call(app, "GET", "/api/ops/morning/settings")).json()["enabled"] is False


# --- The app's own task stack carries the tool --------------------------------


async def test_the_server_task_stack_runs_the_morning_briefing(tmp_path: Path) -> None:
    """``WebServer._init_task_stack`` on a slim stand-in: the runner gets the
    private Ops tool and reaches it through the brain's ToolExecutor."""
    import asyncio

    from jarvis.control.cancel import CancelToken
    from jarvis.core.config import SafetyConfig
    from jarvis.ops.morning import TOOL_NAME, MorningSettings, apply_schedule
    from jarvis.safety.approval import ApprovalWorkflow
    from jarvis.safety.risk_tier import RiskTierEvaluator
    from jarvis.safety.tool_executor import ToolExecutor
    from jarvis.ui.web.server import WebServer

    bus = EventBus()
    data_dir = tmp_path / "data"
    brain = SimpleNamespace(
        _tool_executor_ref=ToolExecutor(
            bus, RiskTierEvaluator(SafetyConfig()), ApprovalWorkflow(bus)
        ),
        _tools={},
    )
    state = SimpleNamespace(
        config=SimpleNamespace(memory=SimpleNamespace(data_dir=str(data_dir))),
        brain=brain,
    )

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    stand_in = SimpleNamespace(
        cfg=SimpleNamespace(memory=SimpleNamespace(data_dir=str(data_dir))),
        bus=bus,
        app=SimpleNamespace(state=state),
        _society_routine_result=_noop,
        _run_society_routine=_noop,
        _guard_society_routine_action=_noop,
    )
    await WebServer._init_task_stack(stand_in)  # type: ignore[arg-type]
    try:
        runner = state.task_runner
        assert TOOL_NAME in runner._tools
        notify = NotifyStore(data_dir / "ops.sqlite")
        await notify.save_settings(enabled=True, kinds=["daily_briefing"])
        ids = await apply_schedule(
            MorningSettings(True, "06:30", "07:00", "UTC", "en"),
            scheduler=state.task_scheduler,
            store=state.task_store,
        )
        await asyncio.wait_for(runner.run(ids[TASK_TAG_PREPARE], CancelToken()), timeout=10)
        assert await notify.outbox() == []  # preparing sends nothing
        await asyncio.wait_for(runner.run(ids[TASK_TAG_OVERVIEW], CancelToken()), timeout=10)
        [row] = await notify.outbox()
        assert (row.kind, row.status, row.transport) == (
            "daily_briefing",
            "simulated",
            "telegram-simulated",
        )
    finally:
        stand_in._task_cancel_token.cancel("test over")
        stand_in._task_scheduler_task.cancel()
        await asyncio.gather(stand_in._task_scheduler_task, return_exceptions=True)
        await state.task_scheduler.shutdown()
        await state.task_store.close()
