"""Independent contracts for the seven Jarvis trigger families."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI

from jarvis.core.bus import EventBus
from jarvis.core.events import WorkflowActivationChanged
from jarvis.core.protocols import RoutineDeferred, current_trigger_path
from jarvis.tasks.cron_schedule import next_cron_ns
from jarvis.tasks.hook_inbox import encode_payload
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.scheduler import TaskScheduler
from jarvis.tasks.schema import AgentAction, TaskSpec, TriggerCron, TriggerSource, WorkflowAction
from jarvis.tasks.source_catalog import catalog
from jarvis.tasks.source_drivers import SourcePacket, listen_file, listen_sse
from jarvis.tasks.source_runtime import SourceSupervisor
from jarvis.tasks.source_schema import SourceSettings
from jarvis.tasks.store import TaskStore
from jarvis.ui.web.control_auth import require_control_key_or_session
from jarvis.ui.web.routine_hooks_routes import router


class Brain:
    def __init__(self):
        self.prompts = []

    async def run_task(self, *, prompt, **kwargs):
        self.prompts.append(prompt)
        return "processed"


@pytest.fixture
async def stack(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    await store.init()
    bus, brain = EventBus(), Brain()
    runner = TaskRunner(store, bus, agent_brain=brain)
    scheduler = TaskScheduler(store, bus, runner)
    scheduler.bind_bus()
    try:
        yield store, scheduler, brain
    finally:
        await scheduler.shutdown()
        await store.close()


def source(kind, **kwargs):
    return TriggerSource(source=SourceSettings(kind=kind, **kwargs))


async def task(scheduler, trigger, name="sample"):
    return await scheduler.schedule(
        TaskSpec(title=name, action=AgentAction(prompt=name), trigger=trigger)
    )


async def finish(scheduler):
    await scheduler._drain_hooks()
    async with asyncio.timeout(5):
        await asyncio.gather(*tuple(scheduler._runner_tasks))


def test_form_large_integer_does_not_overflow_float_validation():
    from jarvis.tasks.source_schema import validate_form

    form = SourceSettings(kind="form", form_fields={"count": {"label": "Count", "kind": "number"}})
    validate_form(form, {"count": 10**400})
    with pytest.raises(ValueError):
        validate_form(form, {"count": float("inf")})


async def test_chat_invocation_requires_owner_user_turn_and_is_absent_in_plan_mode(stack):
    from types import SimpleNamespace

    from jarvis.agent_chat.folder_tools import plan_filter
    from jarvis.core.protocols import ChatTurn, current_chat_turn
    from jarvis.society.conversation_tool import RoutineInvokeTool, RoutineListTool

    store, scheduler, _ = stack
    spec = TaskSpec(
        title="Owner input",
        action=AgentAction(prompt="Process input"),
        trigger=source("chat"),
        tags=("society", "agent:owner"),
    )
    task_id = await scheduler.schedule(spec)

    class Roster:
        async def get(self, agent_id):
            return SimpleNamespace(state="active", session_id="owner-chat")

    class State:
        async def kill_switch(self):
            return False

    runtime = SimpleNamespace(
        roster=Roster(), store=State(), task_services=lambda: (store, scheduler)
    )
    invoke = RoutineInvokeTool(runtime, "owner")
    listing = RoutineListTool(runtime, "owner")
    assert plan_filter({invoke.name: invoke, listing.name: listing}) == {listing.name: listing}
    for session_id, direct_user, allowed in (
        ("owner-chat", False, False),
        ("other-chat", True, False),
        ("owner-chat", True, True),
    ):
        context = current_chat_turn.set(
            ChatTurn(session_id, "turn", "Run it", direct_user, "trace")
        )
        try:
            result = await invoke.execute({"task_id": task_id, "payload": {"value": 1}}, None)
            assert result.success is allowed
        finally:
            current_chat_turn.reset(context)
    assert (await store.hooks.counts(task_id))[1] == 1


def test_seven_groups_have_real_source_definitions():
    rows = catalog()
    assert {row["group"] for row in rows} == {
        "human",
        "time",
        "api",
        "external",
        "stream",
        "system",
        "internal",
    }
    assert {row["id"] for row in rows} >= {
        "manual",
        "chat",
        "form",
        "cron",
        "mcp",
        "github",
        "linear",
        "gmail",
        "slack",
        "stripe",
        "sse",
        "kafka",
        "rabbitmq",
        "mqtt",
        "redis",
        "file",
        "workflow",
    }


@pytest.mark.parametrize("mode", ["manual", "chat", "mcp"])
async def test_human_and_api_input_is_durable_and_mode_scoped(stack, mode):
    store, scheduler, brain = stack
    tid = await task(scheduler, source(mode))
    assert await scheduler.invoke_source(tid, {"number": 4}, "one", mode=mode) == "queued"
    with pytest.raises(ValueError):
        await scheduler.invoke_source(tid, {}, "wrong", mode="form")
    await finish(scheduler)
    assert '"number": 4' in brain.prompts[0]
    assert await store.hooks.counts(tid) == (1, 0)
    assert await scheduler.invoke_source(tid, {"number": 4}, "one", mode=mode) == "duplicate"


async def test_form_types_required_fields_and_http_entry(stack):
    store, scheduler, brain = stack
    tid = await task(
        scheduler,
        source(
            "form",
            form_fields={
                "name": {"label": "Name"},
                "count": {"label": "Count", "kind": "number"},
                "urgent": {"label": "Urgent", "kind": "boolean"},
            },
        ),
    )
    for payload in (
        {},
        {"name": "Ada", "count": "two", "urgent": True},
        {"name": "Ada", "count": 2, "urgent": "yes"},
    ):
        with pytest.raises(ValueError):
            await scheduler.invoke_source(tid, payload, "bad", mode="form")
    app = FastAPI()
    app.include_router(router)
    app.state.task_store, app.state.task_scheduler = store, scheduler

    async def authenticated():
        return None

    app.dependency_overrides[require_control_key_or_session] = authenticated
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/tasks/{tid}/invoke",
            json={"payload": {"name": "Ada", "count": 2, "urgent": False}},
        )
    assert response.status_code == 202, response.text
    await finish(scheduler)
    assert '"urgent": false' in brain.prompts[0]


def test_cron_preserves_local_time_across_dst():
    def ns(value):
        return int(datetime.fromisoformat(value).timestamp()) * 1_000_000_000

    assert next_cron_ns("0 8 * * *", "America/Los_Angeles", ns("2026-03-07T16:00:00+00:00")) == ns(
        "2026-03-08T15:00:00+00:00"
    )
    assert next_cron_ns("30 2 * * *", "Europe/Berlin", ns("2026-03-28T01:30:00+00:00")) == ns(
        "2026-03-30T00:30:00+00:00"
    )
    with pytest.raises(ValueError):
        TriggerCron(expression="* * * * * *", timezone="UTC")


async def test_file_source_reports_changes_and_reuses_checkpoint(tmp_path):
    settings = SourceSettings(kind="file", path=str(tmp_path), poll_seconds=1)
    listener = listen_file(settings, {}, "fixture", {})
    try:
        baseline = await anext(listener)
        assert baseline.checkpoint["baseline"]
        file = tmp_path / "note.txt"
        file.write_text("first", encoding="utf-8")
        created = await asyncio.wait_for(anext(listener), 3)
        assert json.loads(created.body_json)["change"] == "created"
        checkpoint = created.checkpoint
    finally:
        await listener.aclose()
    restarted = listen_file(settings, {}, "fixture", checkpoint)
    try:
        file.write_text("longer revised text", encoding="utf-8")
        changed = await asyncio.wait_for(anext(restarted), 3)
        assert json.loads(changed.body_json)["change"] == "modified"
    finally:
        await restarted.aclose()


async def test_sse_short_frames_are_delivered_without_waiting_for_large_chunks(monkeypatch):
    from jarvis.core.http_pool import HttpClientPool
    from jarvis.tasks import source_drivers

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'id: 42\nevent: changed\ndata: {"x":1}\n\n'
            await asyncio.Event().wait()

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())

    monkeypatch.setattr(
        source_drivers,
        "HttpClientPool",
        lambda **kw: HttpClientPool(transport=httpx.MockTransport(handler)),
    )
    listener = listen_sse(
        SourceSettings(kind="sse", endpoint="https://events.example/stream"),
        {"token": "fixture-token"},
        "fixture",
        {"last_event_id": "41"},
    )
    try:
        assert (await anext(listener)).ready
        message = await asyncio.wait_for(anext(listener), 1)
        assert message.delivery_id == "42" and json.loads(message.body_json)["data"] == {"x": 1}
        assert seen[0].headers["Last-Event-ID"] == "41"
    finally:
        await listener.aclose()


@pytest.mark.parametrize(
    "kind,endpoint",
    [
        ("sse", "https://events.example/"),
        ("kafka", "kafka://localhost:9092"),
        ("rabbitmq", "amqp://localhost:5672"),
        ("mqtt", "mqtt://localhost:1883"),
        ("redis", "redis://localhost:6379"),
    ],
)
async def test_every_listener_admits_before_ack_and_closes(stack, monkeypatch, kind, endpoint):
    from jarvis.tasks import source_credentials

    store, scheduler, _ = stack
    acknowledged, closed = asyncio.Event(), asyncio.Event()

    async def driver(settings, credentials, identity, cursor):
        try:
            yield SourcePacket("ready", "{}", ready=True)

            async def ack():
                assert await store.hooks.counts(tid) == (1, 1)
                acknowledged.set()

            yield SourcePacket("unique", encode_payload({"data": {"value": 7}}), ack)
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(source_credentials, "read", lambda row: {})
    scheduler.sources = SourceSupervisor(store, scheduler.receive_hook, drivers={kind: driver})
    tid = await task(
        scheduler, source(kind, endpoint=endpoint, topic="items" if kind != "sse" else "")
    )
    await asyncio.wait_for(acknowledged.wait(), 3)
    await scheduler.pause(tid)
    await asyncio.wait_for(closed.wait(), 1)


async def test_workflow_chain_carries_output_and_refuses_cycles(stack):
    store, scheduler, brain = stack
    a = await task(scheduler, source("manual"), "first")
    b = await task(scheduler, source("workflow", upstream_id=a), "second")
    await scheduler.invoke_source(a, {}, "start", mode="manual")
    await finish(scheduler)
    await finish(scheduler)
    assert len(brain.prompts) == 2
    assert '"output": "processed"' in brain.prompts[1]
    old = await store.get_spec(a)
    with pytest.raises(ValueError, match="cycle"):
        await scheduler.update_task(
            a, old.model_copy(update={"trigger": source("workflow", upstream_id=b)})
        )
    assert (await store.get(a))["state"] == "scheduled"


async def test_busy_agent_delivery_is_deferred_without_failure_or_second_admission(stack):
    store, scheduler, _ = stack
    calls = []

    async def owned(*args):
        calls.append(1)
        if len(calls) == 1:
            raise RoutineDeferred("busy")
        return "done"

    scheduler.attach_runner(TaskRunner(store, EventBus(), owned_agent_runner=owned))
    spec = TaskSpec(
        title="busy",
        trigger=source("chat"),
        action=AgentAction(prompt="work"),
        tags=("agent:sample",),
    )
    tid = await scheduler.schedule(spec)
    await scheduler.invoke_source(tid, {}, "one", mode="chat")
    await finish(scheduler)
    assert await store.hooks.counts(tid) == (1, 1)
    scheduler._hook_retry_at[tid] = 0
    await finish(scheduler)
    assert await store.hooks.counts(tid) == (1, 0)
    assert (await store.get(tid))["last_error"] is None


async def test_native_workflow_activation_is_an_explicit_event(stack):
    from uuid import uuid4

    store, scheduler, brain = stack
    wid = str(uuid4())

    class Workflows:
        async def get_workflow(self, workflow_id):
            return {"id": workflow_id, "enabled": True}

    scheduler._workflow_services = lambda: (Workflows(), None)
    tid = await task(
        scheduler, source("workflow", upstream_id=wid, upstream_kind="workflow", when="activated")
    )
    await scheduler._bus.publish(WorkflowActivationChanged(workflow_id=wid, enabled=False))
    assert await store.hooks.counts(tid) == (0, 0)
    await scheduler._bus.publish(WorkflowActivationChanged(workflow_id=wid, enabled=True))
    await finish(scheduler)
    assert len(brain.prompts) == 1


def test_source_configuration_rejects_credentials_in_urls():
    with pytest.raises(ValueError, match="credentials"):
        SourceSettings(kind="sse", endpoint="https://user:secret@events.example/")


async def test_native_workflow_dispatch_receives_trusted_ancestry(stack):
    from uuid import uuid4

    store, scheduler, _ = stack
    calls = []

    class Workflows:
        async def get_workflow(self, workflow_id):
            return {"id": workflow_id, "enabled": True}

        async def trigger(self, workflow_id, **kwargs):
            calls.append((workflow_id, kwargs, current_trigger_path.get()))
            return "child-run"

    workflows = Workflows()
    scheduler._workflow_services = lambda: (workflows, workflows)
    scheduler.attach_runner(
        TaskRunner(store, EventBus(), workflow_services=lambda: (workflows, workflows))
    )
    tid = await scheduler.schedule(
        TaskSpec(
            title="native", trigger=source("manual"), action=WorkflowAction(workflow_id=uuid4())
        )
    )
    await scheduler.invoke_source(tid, {"value": 7}, "one", mode="manual")
    await finish(scheduler)
    assert calls[0][1]["input_data"] == {"value": 7}
    assert calls[0][2] == ("task:" + tid,)
    assert current_trigger_path.get() == ()


@pytest.mark.parametrize(
    "owner_state,halted,with_guard,allowed",
    [
        ("active", False, True, True),
        ("paused", False, True, False),
        ("active", True, True, False),
        ("active", False, False, False),
    ],
)
async def test_owned_workflow_dispatch_honors_live_owner_state(
    stack, owner_state, halted, with_guard, allowed
):
    from types import SimpleNamespace
    from uuid import uuid4

    from jarvis.society.routine_runner import guard_owned_routine

    store, scheduler, _ = stack
    calls = []

    class Workflows:
        async def get_workflow(self, workflow_id):
            return {"id": workflow_id, "enabled": True}

        async def trigger(self, workflow_id, **kwargs):
            calls.append(workflow_id)
            return "child-run"

    class Roster:
        async def get(self, agent_id):
            return SimpleNamespace(state=owner_state)

    class State:
        async def kill_switch(self):
            return halted

    runtime = SimpleNamespace(roster=Roster(), store=State())

    async def guard(tags):
        await guard_owned_routine(runtime, tags)

    workflows = Workflows()
    scheduler._workflow_services = lambda: (workflows, workflows)
    scheduler.attach_runner(
        TaskRunner(
            store,
            EventBus(),
            workflow_services=lambda: (workflows, workflows),
            owned_action_guard=guard if with_guard else None,
        )
    )
    tid = await scheduler.schedule(
        TaskSpec(
            title="Owned workflow",
            trigger=source("manual"),
            action=WorkflowAction(workflow_id=uuid4()),
            tags=("society", "agent:owner"),
        )
    )
    await scheduler.invoke_source(tid, {}, "owner-check", mode="manual")
    await finish(scheduler)
    assert bool(calls) is allowed
    assert bool((await store.get(tid))["last_error"]) is not allowed
