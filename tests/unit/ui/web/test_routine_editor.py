"""Routine editing exercises the real store and scheduler without external providers."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from jarvis.core.bus import EventBus
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.scheduler import TaskScheduler
from jarvis.tasks.store import TaskStore
from jarvis.ui.web.society_routes import router


class EditorRuntime:
    def __init__(self):
        self.roster = self

    async def ensure_started(self):
        pass  # No background society workers are needed for route tests.

    async def resolve(self, agent_id):
        if agent_id not in ("mail", "other"):
            return None
        return SimpleNamespace(agent_id=agent_id, name=agent_id, title="", description="", focus=[])


@pytest.fixture
async def editor(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    await store.init()
    app = FastAPI()
    app.include_router(router)
    app.state.society = EditorRuntime()
    app.state.task_store = store
    app.state.task_scheduler = TaskScheduler(store, EventBus())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, store, app.state.task_scheduler
    await store.close()


BODY = {
    "title": "Inbox",
    "prompt": "Read the inbox.",
    "schedule": {"type": "calendar", "local_time": "08:00", "timezone": "Europe/Berlin"},
    "plugin_grants": [{"plugin_id": "gmail", "scope": "read"}],
    "announce_on_success": "Inbox ready",
}
URL = "/api/society/agents/mail/routines"


async def test_update_preserves_identity_permissions_history_and_pause(editor):
    client, store, scheduler = editor
    response = await client.post(URL, json=BODY)
    assert response.status_code == 200, response.text
    task_id = response.json()["id"]
    old = await store.get_spec(task_id)
    await store.append_step(task_id, "log", {"event": "agent_result", "text": "Previous run"})
    await scheduler.pause(task_id)
    update = {k: v for k, v in BODY.items() if k in ("title", "prompt", "schedule")}
    update.update(title="Updated", prompt="New instruction")
    update["schedule"] = {**BODY["schedule"], "local_time": "09:30"}
    response = await client.patch(f"{URL}/{task_id}", json=update)
    assert response.status_code == 200, response.text
    spec = await store.get_spec(task_id)
    row = await store.get(task_id)
    assert spec.id == old.id and spec.tags == old.tags
    assert spec.action.plugin_grants == old.action.plugin_grants
    assert spec.announce_on_success == old.announce_on_success
    assert spec.action.prompt.endswith("New instruction")
    assert spec.trigger.local_time == "09:30"
    assert row["state"] == "paused"
    assert row["steps"][0]["payload"]["text"] == "Previous run"
    assert (
        await client.patch(f"/api/society/agents/other/routines/{task_id}", json=update)
    ).status_code == 404
    await store.update_state(task_id, "running")
    assert (await client.patch(f"{URL}/{task_id}", json=update)).status_code == 409


async def test_add_schedule_inherits_action_and_stays_paused(editor):
    client, store, scheduler = editor
    root = (await client.post(URL, json=BODY)).json()["id"]
    await scheduler.pause(root)
    response = await client.post(
        URL,
        json={
            **BODY,
            "parent_task_id": root,
            "prompt": "Must not replace the action",
            "plugin_grants": [{"plugin_id": "gmail", "scope": "write"}],
            "schedule": {"type": "every", "interval_seconds": 7200},
        },
    )
    assert response.status_code == 200, response.text
    child = response.json()["id"]
    parent_spec, child_spec = await store.get_spec(root), await store.get_spec(child)
    assert child_spec.action == parent_spec.action
    assert f"routine-group:{root}" in child_spec.tags
    assert (await store.get(child))["state"] == "paused"
    assert child not in [task_id for _, task_id in scheduler._heap]
    response = await client.post(
        "/api/society/agents/other/routines", json={**BODY, "parent_task_id": root}
    )
    assert response.status_code == 404


async def test_invalid_schedule_cannot_alter_existing_routine(editor):
    client, store, _ = editor
    root = (await client.post(URL, json=BODY)).json()["id"]
    old = await store.get_spec(root)
    response = await client.patch(
        f"{URL}/{root}",
        json={
            "title": "Bad",
            "prompt": "Bad",
            "schedule": {"type": "calendar", "local_time": "25:00"},
        },
    )
    assert response.status_code == 422
    assert await store.get_spec(root) == old


async def test_runner_records_each_success_and_failure(editor):
    client, store, _ = editor
    root = (await client.post(URL, json=BODY)).json()["id"]

    class Brain:
        failing = False

        async def run_task(self, **kwargs):
            if self.failing:
                raise RuntimeError("Example failure")
            return "Example result"

    brain = Brain()
    runner = TaskRunner(store, EventBus(), agent_brain=brain)
    await runner.run(root)
    brain.failing = True
    await runner.run(root)
    events = [step["payload"].get("event") for step in (await store.get(root))["steps"]]
    assert events.count("run_started") == 2
    assert events.count("run_completed") == 1
    assert events.count("error") == 1
    assert (await store.get(root))["state"] == "scheduled"
