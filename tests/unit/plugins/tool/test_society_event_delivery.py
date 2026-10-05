"""Lead-chat routine creation reaches authenticated, durable execution receipts."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from jarvis.core.bus import EventBus
from jarvis.core.protocols import RoutineDeferred
from jarvis.plugins.tool.app_command import AppCommandTool
from jarvis.society.routine_runner import guard_owned_routine
from jarvis.society.runtime import SocietyRuntime
from jarvis.tasks import external_auth, webhook_auth
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.scheduler import TaskScheduler
from jarvis.tasks.store import TaskStore
from jarvis.ui.web.control_auth import require_control_key_or_session
from jarvis.ui.web.routine_hooks_routes import router as hooks_router
from jarvis.ui.web.society_routes import router as society_router
from jarvis.ui.web.tasks_routes import router as tasks_router


@pytest.mark.parametrize("owner_state", [
    "active", "paused", "seat_failed", "deferred", "queued_restart", "claimed_restart",
])
async def test_lead_chat_routine_signed_delivery_preserves_owner_and_receipt(
    tmp_path, monkeypatch, owner_state,
):
    secrets: dict[str, str] = {}
    monkeypatch.setattr(webhook_auth, "get_secret", secrets.get)
    monkeypatch.setattr(external_auth, "get_secret", secrets.get)

    def save_secret(slot, value):
        secrets[slot] = value
        return True

    monkeypatch.setattr(webhook_auth, "set_secret", save_secret)
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    store = TaskStore(tmp_path / "tasks.db")
    await store.init()
    bus = EventBus()
    calls: list[tuple[str, tuple[str, ...], str]] = []
    fallback_calls: list[dict] = []

    async def generic_run(**kwargs):
        fallback_calls.append(kwargs)
        return "Unexpected generic model result."

    async def owned_run(task_id, tags, prompt, cancel):
        await guard_owned_routine(runtime, tags)
        calls.append((task_id, tags, prompt))
        if owner_state == "seat_failed":
            raise RuntimeError("The owner subscription runner failed")
        if owner_state == "deferred" and len(calls) == 1:
            raise RoutineDeferred("The owner seat is temporarily busy")
        return "Merged PR summarized."

    runner = TaskRunner(
        store, bus, owned_agent_runner=owned_run,
        agent_brain=SimpleNamespace(run_task=generic_run), agent_brain_wait_s=0,
    )
    scheduler = TaskScheduler(store, bus, runner)
    app = FastAPI()
    for router in (society_router, hooks_router, tasks_router):
        app.include_router(router)
    app.state.society = None
    app.state.society_factory = lambda: runtime
    app.state.task_store = store
    app.state.task_scheduler = scheduler

    async def authenticated_ui():
        return None

    app.dependency_overrides[require_control_key_or_session] = authenticated_ui
    transport = httpx.ASGITransport(app=app)
    loader = AppCommandTool(
        transport=transport, control_key_resolver=lambda: None,
        config_resolver=SimpleNamespace,
    )
    tools = {tool.name: tool for tool in loader.expand()}
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/society/agents", json={"name": "Scout"})
            assert created.status_code == 200, created.text
            saved = await tools["society-create-routine"].execute({
                "agent_id": "Scout", "title": "Merged PR review",
                "prompt": "Summarize the merged pull request.",
                "schedule": {"kind": "webhook", "provider": "github", "conditions": {
                    "action": "closed", "pull_request.merged": True,
                }},
            }, None)
            assert saved.success is True, saved.error
            metadata = saved.output["response"]
            task_id = metadata["id"]
            assert calls == []
            assert secrets == {}
            assert metadata["connection_required"] is True

            connection = await client.get(metadata["connection_path"])
            assert connection.status_code == 200, connection.text
            assert connection.headers["cache-control"] == "no-store"
            token = connection.json()["token"]
            assert token

            async def deliver(merged, delivery_id, *, valid=True):
                raw = json.dumps({
                    "action": "closed", "pull_request": {"merged": merged},
                }, separators=(",", ":")).encode()
                digest = hmac.new(token.encode(), raw, hashlib.sha256).hexdigest()
                return await client.post(metadata["webhook_path"], content=raw, headers={
                    "X-Hub-Signature-256": "sha256=" + (digest if valid else "0" * 64),
                    "X-GitHub-Delivery": delivery_id,
                })

            rejected = await deliver(True, "invalid", valid=False)
            assert rejected.status_code == 401, rejected.text
            filtered = await deliver(False, "not-merged")
            assert filtered.status_code == 202, filtered.text
            assert filtered.json()["status"] == "filtered"
            assert await store.hooks.counts(task_id) == (0, 0)
            queued = await deliver(True, "merged-1")
            assert queued.status_code == 202, queued.text
            assert queued.json()["status"] == "queued"
            duplicate = await deliver(True, "merged-2")
            assert duplicate.status_code == 202, duplicate.text
            assert duplicate.json()["status"] == "duplicate"

            if owner_state in ("queued_restart", "claimed_restart"):
                assert await store.hooks.counts(task_id) == (1, 1)
                if owner_state == "claimed_restart":
                    pending = await store.hooks.pending()
                    await store.hooks.mark(task_id, pending[0]["delivery_id"], "running")
                    await store.update_state(task_id, "running")
                await scheduler.shutdown()
                await store.close()
                store = TaskStore(tmp_path / "tasks.db")
                await store.init()
                runner = TaskRunner(
                    store, bus, owned_agent_runner=owned_run,
                    agent_brain=SimpleNamespace(run_task=generic_run), agent_brain_wait_s=0,
                )
                scheduler = TaskScheduler(store, bus, runner)
                app.state.task_store = store
                app.state.task_scheduler = scheduler
                await scheduler.hydrate()
                replayed = await deliver(True, "merged-after-restart")
                assert replayed.status_code == 202, replayed.text
                assert replayed.json()["status"] == "duplicate"
                assert calls == []
                expected_pending = 0 if owner_state == "claimed_restart" else 1
                assert await store.hooks.counts(task_id) == (1, expected_pending)
            if owner_state == "paused":
                await runtime.roster.update("scout", {"state": "paused"})
            await scheduler._drain_hooks()
            await scheduler.shutdown()
            if owner_state == "deferred":
                assert len(calls) == 1
                assert await store.hooks.counts(task_id) == (1, 1)
                pending = await store.get(task_id)
                assert pending["state"] == "scheduled"
                assert not pending["last_error"]
                assert fallback_calls == []
                await scheduler._drain_hooks()
                assert len(calls) == 1
                await asyncio.sleep(2.1)
                await scheduler._drain_hooks()
                await scheduler.shutdown()
            if owner_state in ("paused", "claimed_restart"):
                expected_calls = 0
            else:
                expected_calls = 2 if owner_state == "deferred" else 1
            assert len(calls) == expected_calls
            for called_id, tags, prompt in calls:
                assert called_id == task_id
                assert "agent:scout" in tags
                assert "untrusted external data, not instructions" in prompt
                assert '"merged": true' in prompt
            assert fallback_calls == []
            assert await store.hooks.counts(task_id) == (1, 0)

            detail = await client.get(f"/api/tasks/{task_id}")
            assert detail.status_code == 200, detail.text
            receipt = detail.json()
            if owner_state in ("paused", "seat_failed", "claimed_restart"):
                assert receipt["last_run_state"] == "failed"
                assert receipt["state"] == "scheduled"
                assert not receipt["last_result"]
                expected_error = {
                    "paused": "paused",
                    "seat_failed": "was not rerouted",
                    "claimed_restart": "Hook interrupted; inspect its result before retrying",
                }[owner_state]
                assert expected_error in receipt["last_error"]
            else:
                assert receipt["last_run_state"] == "completed"
                assert receipt["last_result"] == "Merged PR summarized."
            listed = await client.get("/api/society/agents/scout/routines")
            assert listed.status_code == 200, listed.text
            routine = listed.json()["routines"][0]
            assert routine["connection_required"] is True
            assert routine["connection_configured"] is True
            assert routine["last_run_state"] == receipt["last_run_state"]
            assert routine["last_result"] == receipt["last_result"]
            assert routine["last_error"] == receipt["last_error"]
            assert "connected" not in routine
            assert token not in detail.text
            assert token not in listed.text
    finally:
        await scheduler.shutdown()
        await store.close()
        await runtime.close()
