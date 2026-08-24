"""``/api/tasks`` — the Automations API contract the UI codes against.

Exercised through the real router on a FastAPI app whose ``app.state`` holds
a real ``TaskStore`` + ``TaskScheduler`` (with a fake runner) and, where a
test needs it, a fake brain exposing ``snapshot()``. No ``unittest.mock``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.core.bus import EventBus
from jarvis.tasks import templates as tpl
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.scheduler import TaskScheduler
from jarvis.tasks.schema import AgentAction, SpeakAction, TaskSpec, TriggerAfterDelay, TriggerEvery
from jarvis.tasks.store import TaskStore
from jarvis.ui.web import tasks_routes


class FakeBrain:
    """The two things the routes touch on ``app.state.brain``."""

    def __init__(self, tools: list[str]) -> None:
        self._tools = tools
        self.calls: list[str] = []

    def snapshot(self) -> dict[str, Any]:
        return {"tools_available": sorted(self._tools)}

    async def run_task(self, *, prompt: str, **_: Any) -> str:
        self.calls.append(prompt)
        return "the answer " * 100   # > 400 chars, so truncation is visible


class Harness:
    def __init__(self, app: FastAPI, store: TaskStore, scheduler: TaskScheduler,
                 brain: FakeBrain) -> None:
        self.app = app
        self.store = store
        self.scheduler = scheduler
        self.brain = brain

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://t",
        )

    async def settle(self) -> None:
        await self.scheduler.shutdown()
        await asyncio.sleep(0)


@pytest.fixture
async def harness(tmp_path: Path):
    store = TaskStore(tmp_path / "routes.db")
    await store.init()
    bus = EventBus()
    brain = FakeBrain(["gmail", "search_web", "github/list_issues", "github/create_issue"])
    runner = TaskRunner(store=store, bus=bus, agent_brain=brain)
    scheduler = TaskScheduler(store=store, bus=bus, runner=runner)
    app = FastAPI()
    app.include_router(tasks_routes.router)
    app.state.task_store = store
    app.state.task_scheduler = scheduler
    app.state.brain = brain
    try:
        yield Harness(app, store, scheduler, brain)
    finally:
        await scheduler.shutdown()
        await store.close()


def _every_body(**overrides: Any) -> dict[str, Any]:
    spec = TaskSpec(
        title="recurring",
        trigger=TriggerEvery(interval_seconds=3600),
        action=AgentAction(prompt="hello"),
        tags=("template:morning_brief", "news"),
        created_by="template",
        **overrides,
    )
    return spec.model_dump(mode="json")


# ----------------------------------------------------------------------
# Templates
# ----------------------------------------------------------------------

async def test_templates_catalogue_is_localized_with_readiness(harness: Harness) -> None:
    async with harness.client() as c:
        res = await c.get("/api/tasks/templates", params={"locale": "de"})
    assert res.status_code == 200
    body = res.json()
    assert body["categories"] == list(tpl.CATEGORIES)
    templates = {t["key"]: t for t in body["templates"]}
    assert templates, "the catalogue must not be empty"
    for key, entry in templates.items():
        template = tpl.get_template(key)
        assert template is not None
        assert entry["name"] == template.name.for_locale("de")
        live = ["gmail", "search_web", "github/list_issues", "github/create_issue"]
        assert entry["missing"] == tpl.missing_requirements(template.requires, live)
        assert entry["ready"] == (not entry["missing"])


async def test_templates_without_brain_default_to_ready(harness: Harness) -> None:
    harness.app.state.brain = None
    async with harness.client() as c:
        body = (await c.get("/api/tasks/templates")).json()
    assert all(t["ready"] and t["missing"] == [] for t in body["templates"])


async def test_templates_route_is_not_captured_as_task_id(harness: Harness) -> None:
    async with harness.client() as c:
        res = await c.get("/api/tasks/templates")
    assert res.status_code == 200
    assert "templates" in res.json()


async def test_add_template_schedules_tagged_task(harness: Harness) -> None:
    key = next(iter(tpl.all_templates()))
    template = tpl.get_template(key)
    assert template is not None
    inputs = {i.key: (i.default or "value") for i in template.inputs}
    async with harness.client() as c:
        res = await c.post(
            f"/api/tasks/templates/{key}/add",
            json={"inputs": inputs, "locale": "de", "title": "  "},
        )
        assert res.status_code == 201, res.text
        tid = res.json()["id"]
        detail = (await c.get(f"/api/tasks/{tid}")).json()
    assert detail["state"] == "scheduled"
    assert detail["trigger_type"] == "every"
    assert detail["created_by"] == "template"
    assert template.tag in detail["tags"]
    assert detail["title"] == template.name.for_locale("de")
    assert detail["interval_seconds"] == detail["spec"]["trigger"]["interval_seconds"]
    assert any(tid == t for _, t in harness.scheduler._heap)


async def test_add_template_unknown_key_404(harness: Harness) -> None:
    async with harness.client() as c:
        res = await c.post("/api/tasks/templates/does_not_exist/add", json={})
    assert res.status_code == 404


async def test_add_template_missing_required_input_422(harness: Harness) -> None:
    required = [
        t for t in tpl.all_templates().values()
        if any(i.required and not i.default for i in t.inputs)
    ]
    if not required:
        pytest.skip("no template with a required input without default")
    async with harness.client() as c:
        res = await c.post(f"/api/tasks/templates/{required[0].key}/add", json={"inputs": {}})
    assert res.status_code == 422
    assert "missing required inputs" in res.json()["detail"]


# ----------------------------------------------------------------------
# Summaries
# ----------------------------------------------------------------------

async def test_list_summaries_carry_automation_fields(harness: Harness) -> None:
    async with harness.client() as c:
        tid = (await c.post("/api/tasks", json=_every_body())).json()["id"]
        first = (await c.get("/api/tasks")).json()["tasks"][0]
    assert first["id"] == tid
    assert first["tags"] == ["template:morning_brief", "news"]
    assert first["created_by"] == "template"
    assert first["interval_seconds"] == 3600.0
    assert first["next_due_at_ns"] == first["due_at_ns"] is not None
    assert first["last_run_state"] is None
    assert first["last_result"] is None


async def test_summaries_reflect_last_run(harness: Harness) -> None:
    async with harness.client() as c:
        tid = (await c.post("/api/tasks", json=_every_body())).json()["id"]
        res = await c.post(f"/api/tasks/{tid}/run")
        assert res.status_code == 200 and res.json() == {"ok": True, "id": tid}
        await harness.settle()
        row = (await c.get("/api/tasks")).json()["tasks"][0]
        detail = (await c.get(f"/api/tasks/{tid}")).json()
    assert harness.brain.calls == ["hello"]
    for out in (row, detail):
        assert out["state"] == "scheduled"
        assert out["last_run_state"] == "completed"
        assert len(out["last_result"]) == tasks_routes.LAST_RESULT_MAX_CHARS
        assert out["last_result"].startswith("the answer")
    assert any(s["payload"].get("event") == "run_now" for s in detail["steps"])


async def test_run_now_409_while_running_and_404_unknown(harness: Harness) -> None:
    async with harness.client() as c:
        tid = (await c.post("/api/tasks", json=_every_body())).json()["id"]
        await harness.store.update_state(tid, "running")
        assert (await c.post(f"/api/tasks/{tid}/run")).status_code == 409
        assert (await c.post("/api/tasks/nope/run")).status_code == 404
        row = (await c.get("/api/tasks")).json()["tasks"][0]
    assert row["last_run_state"] == "running"


async def test_run_now_without_scheduler_503(harness: Harness) -> None:
    harness.app.state.task_scheduler = None
    tid = await harness.store.insert(TaskSpec(**{
        k: v for k, v in _every_body().items() if k != "id"
    }))
    async with harness.client() as c:
        assert (await c.post(f"/api/tasks/{tid}/run")).status_code == 503


# ----------------------------------------------------------------------
# PATCH enabled
# ----------------------------------------------------------------------

async def test_patch_pauses_and_resumes(harness: Harness) -> None:
    async with harness.client() as c:
        tid = (await c.post("/api/tasks", json=_every_body())).json()["id"]
        res = await c.patch(f"/api/tasks/{tid}", json={"enabled": False})
        assert res.status_code == 200
        assert res.json()["state"] == "paused"
        assert (await c.get("/api/tasks")).json()["tasks"][0]["state"] == "paused"
        assert all(tid != t for _, t in harness.scheduler._heap)

        res = await c.patch(f"/api/tasks/{tid}", json={"enabled": True})
        assert res.status_code == 200
        assert res.json()["state"] == "scheduled"
        assert res.json()["due_at_ns"] is not None
        assert any(tid == t for _, t in harness.scheduler._heap)


async def test_patch_rejects_one_shot_unknown_and_running(harness: Harness) -> None:
    async with harness.client() as c:
        one_shot = TaskSpec(
            title="once", trigger=TriggerAfterDelay(delay_seconds=60),
            action=SpeakAction(text="x"),
        ).model_dump(mode="json")
        oid = (await c.post("/api/tasks", json=one_shot)).json()["id"]
        assert (await c.patch(f"/api/tasks/{oid}", json={"enabled": False})).status_code == 409
        assert (await c.patch("/api/tasks/nope", json={"enabled": False})).status_code == 404

        tid = (await c.post("/api/tasks", json=_every_body())).json()["id"]
        await harness.store.update_state(tid, "running")
        assert (await c.patch(f"/api/tasks/{tid}", json={"enabled": False})).status_code == 409
        # resume of a task that is not paused
        await harness.store.update_state(tid, "scheduled")
        assert (await c.patch(f"/api/tasks/{tid}", json={"enabled": True})).status_code == 409
        # malformed body (unknown field — the request model forbids extras)
        assert (await c.patch(f"/api/tasks/{tid}", json={"on": True})).status_code == 422


async def test_paused_task_can_be_cancelled_then_deleted(harness: Harness) -> None:
    async with harness.client() as c:
        tid = (await c.post("/api/tasks", json=_every_body())).json()["id"]
        await c.patch(f"/api/tasks/{tid}", json={"enabled": False})
        assert (await c.delete(f"/api/tasks/{tid}")).status_code == 409
        assert (await c.post(f"/api/tasks/{tid}/cancel")).status_code == 200
        assert (await c.delete(f"/api/tasks/{tid}")).status_code == 200
