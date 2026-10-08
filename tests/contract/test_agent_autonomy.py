"""Goal-derived work crosses the real MCP gate, task store and scheduler.

The model/CLI transport is scripted. These contracts do not claim installed
provider or desktop execution; each runtime uses the same owned-tool boundary.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from jarvis.agent_chat.store import AgentChatStore
from jarvis.agent_chat.tool_context import register_turn, unregister_turn
from jarvis.agent_chat.turn_completion import TurnCompletion
from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core import runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.protocols import ChatTurn, current_chat_turn
from jarvis.mcp.jarvis_tools_server import build_server
from jarvis.society.routine_runner import run_owned_routine
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.surface import tools_for_cli_session
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.scheduler import TaskScheduler
from jarvis.tasks.store import TaskStore
from tests.fakes.fake_agent_chat import FakeChatService
from tests.unit.society.test_cli_routines import ExecutingFake, call


@pytest.fixture(params=["jarvis", "hermes", "openclaw"])
async def world(request, tmp_path, monkeypatch):
    w = SimpleNamespace(runtime_name=request.param)
    w.clock = SimpleNamespace(now_ns=time.time_ns())
    clock = SimpleNamespace(time_ns=lambda: w.clock.now_ns, monotonic=time.monotonic)
    monkeypatch.setattr("jarvis.tasks.store.time", clock)
    monkeypatch.setattr("jarvis.tasks.scheduler.time", clock)
    w.store = AgentChatStore(tmp_path / "chat.db")
    service = FakeChatService(w.store)
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path)))
    w.rt = SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: service, cfg=lambda: cfg
    )
    await w.rt.ensure_started()
    agent, _ = await w.rt.roster.create(name="Scout", runtime=request.param, provider="ollama")
    w.store.create_session(
        session_id=agent.session_id,
        surface="society",
        provider="ollama",
        model="scripted",
        effort="",
        cwd=str(tmp_path),
        permission_mode="bypass",
        runtime=request.param,
    )
    w.task_path = tmp_path / "autonomy.db"
    w.tasks = TaskStore(w.task_path)
    await w.tasks.init()
    w.scheduler = TaskScheduler(store=w.tasks, bus=EventBus())
    w.rt.task_services = lambda: (w.tasks, w.scheduler)
    executor = ExecutingFake()
    manager = SimpleNamespace(_tools={}, _tool_executor=executor, _config=cfg)

    async def scoped(session_id, tools):
        return await tools_for_cli_session(session_id, tools, manager)

    w.gateway = BrainSupervisorToolGateway(manager, session_tools=scoped)
    previous = runtime_refs.get_supervisor_tool_gateway()
    runtime_refs.set_supervisor_tool_gateway(w.gateway)
    w.server = build_server()
    try:
        yield w
    finally:
        runtime_refs.set_supervisor_tool_gateway(previous)
        await w.scheduler.shutdown()
        await w.tasks.close()
        await w.rt.close()
        w.store.close()


def arguments(text="Keep me informed about this project", *, schedule=None, **payload):
    return {
        "mode": "autonomous",
        "kind": "routine",
        "request_quote": text,
        "reason": "A follow-up keeps the requested project information current.",
        "payload": {
            "title": "Project follow-up",
            "prompt": "Check the project and report new findings.",
            "schedule": schedule or {"kind": "after_delay", "delay_seconds": 60},
            **payload,
        },
    }


async def submit(w, args, *, text=None, direct=True, turn_id="t1"):
    origin = ChatTurn("society:scout", turn_id, text or args["request_quote"], direct, "trace")
    token = current_chat_turn.set(origin)
    registration = register_turn(origin.session_id)
    current_chat_turn.reset(token)
    try:
        result = await call(w, "society_propose_change", args)
        try:
            return json.loads(result)
        except ValueError:
            return {"error": result}
    finally:
        unregister_turn(origin.session_id, registration)


async def test_one_time_commitment_is_durable_without_approval_card(world):
    result = await submit(world, arguments())
    assert result.get("applied"), result
    task_id = result["task_id"]
    row = await world.tasks.get(task_id)
    assert row["state"] == "scheduled" and result["next_run"]
    assert task_id in world.scheduler._known
    spec = await world.tasks.get_spec(task_id)
    assert spec.trigger.type == "after_delay"
    origin = json.loads(next(t[7:] for t in spec.tags if t.startswith("origin:")))
    assert origin["turn_id"] == "t1" and origin["request_quote"] == arguments()["request_quote"]
    assert not await world.rt.approvals.items(state="pending")
    assert not any(n.get("kind") == "proposal" for _, n in world.rt.chat_service().notices)


async def test_inferred_recurring_goal_is_active_without_routine_keyword(world):
    result = await submit(world, arguments(schedule={"kind": "every", "interval_seconds": 3600}))
    spec = await world.tasks.get_spec(result["task_id"])
    assert spec.trigger.type == "every" and result["state"] == "scheduled"


@pytest.mark.parametrize(
    "text",
    [
        "If the project changes, check it again and keep me informed",
        "Wenn sich das Projekt ändert, prüfe es und halte mich auf dem Laufenden",  # i18n-allow
    ],
)
async def test_conditional_instructions_are_not_hypothetical_questions(world, text):
    result = await submit(
        world, arguments(text, schedule={"kind": "every", "interval_seconds": 60})
    )
    assert result.get("applied"), result


async def test_no_routine_still_allows_a_requested_one_time_followup(world):
    result = await submit(world, arguments("Check it later only once, no routine"))
    assert result.get("applied"), result
    assert result["trigger"]["type"] == "after_delay"


async def test_duplicate_delivery_and_restart_keep_id_and_original_deadline(world):
    first = await submit(world, arguments())
    tid = first["task_id"]
    deadline = (await world.tasks.get(tid))["due_at_ns"]
    world.clock.now_ns += 20_000_000_000
    await world.tasks.close()
    world.tasks = TaskStore(world.task_path)
    await world.tasks.init()
    world.scheduler = TaskScheduler(store=world.tasks, bus=EventBus())
    await world.scheduler.hydrate()
    second = await submit(world, arguments(), turn_id="redelivery")
    assert second["task_id"] == tid and second["reused"]
    assert (await world.tasks.get(tid))["due_at_ns"] == deadline
    assert len(await world.tasks.list()) == 1
    assert world.scheduler._heap == [(deadline, tid)]


async def test_pause_resume_update_to_one_time_and_cancel_preserve_identity(world):
    original = arguments(schedule={"kind": "every", "interval_seconds": 3600})
    tid = (await submit(world, original))["task_id"]
    for operation, expected in [("pause", "paused"), ("resume", "scheduled")]:
        outcome = await submit(world, arguments(operation=operation, task_id=tid))
        assert outcome["state"] == expected
    changed = await submit(
        world,
        arguments(
            "Only this time",
            operation="update",
            task_id=tid,
            schedule={"kind": "after_delay", "delay_seconds": 120},
        ),
    )
    assert changed["task_id"] == tid and changed["trigger"]["type"] == "after_delay"
    deadline = (await world.tasks.get(tid))["due_at_ns"]
    await submit(world, arguments(operation="pause", task_id=tid))
    await submit(world, arguments(operation="resume", task_id=tid))
    assert (await world.tasks.get(tid))["due_at_ns"] == deadline
    outcome = await submit(world, arguments("Stop that work", operation="cancel", task_id=tid))
    assert outcome["state"] == "cancelled"
    again = await submit(world, original)
    assert again["state"] == "cancelled" and again["reused"]
    assert tid not in world.scheduler._known


@pytest.mark.parametrize(
    "text",
    [
        "Explain how to check the project later",
        '"Check the project later"',
        "If I wanted to monitor the project, what would happen?",
        "Check this project but do not schedule anything",
        "Nur diesmal prüfen",  # i18n-allow: user input
    ],
)
async def test_non_actions_and_explicit_exclusions_do_not_create_jobs(world, text):
    result = await submit(
        world, arguments(text, schedule={"kind": "every", "interval_seconds": 60})
    )
    assert not result.get("applied"), result
    assert await world.tasks.list() == []


async def test_forged_background_and_read_only_requests_are_refused(world):
    result = await submit(world, arguments(), direct=False)
    assert not result.get("applied"), result
    result = await submit(world, arguments(), text="Unrelated current request")
    assert not result.get("applied"), result
    world.store.update_session("society:scout", permission_mode="plan")
    result = await submit(world, arguments())
    assert not result.get("applied"), result
    assert await world.tasks.list() == []


async def test_interrupted_effects_are_not_replayed_at_restart(world):
    tid = (await submit(world, arguments()))["task_id"]
    await world.tasks.update_state(tid, "running")
    await world.tasks.append_step(tid, "log", {"event": "effect_started", "receipt": "r1"})
    assert await world.tasks.cleanup_interrupted() == 1
    restarted = TaskScheduler(store=world.tasks, bus=EventBus())
    await restarted.hydrate()
    assert tid not in restarted._known
    assert (await submit(world, arguments()))["state"] == "interrupted"


async def test_paid_owner_waits_without_starting_a_turn_or_fallback(world):
    await world.rt.roster.update("scout", {"provider": "openai"})
    tid = (await submit(world, arguments()))["task_id"]
    notices = []

    async def owned(task_id, tags, prompt, cancel):
        return await run_owned_routine(world.rt, task_id, tags, prompt, cancel)

    async def sink(tags, text, status):
        notices.append((text, status))

    runner = TaskRunner(
        store=world.tasks,
        bus=EventBus(),
        harness_manager=None,
        tts=None,
        tool_executor=None,
        tool_registry={},
        owned_agent_runner=owned,
        result_sink=sink,
    )
    await runner.run(tid)
    row = await world.tasks.get(tid)
    assert row["state"] == "paused" and "subscription" in row["last_error"]
    assert notices[-1][1] == "waiting"
    assert world.rt.chat_service().sent == []


@pytest.mark.parametrize("paused", [False, True])
async def test_runtime_run_reports_its_result_in_the_origin_chat(world, monkeypatch, paused):
    tid = (await submit(world, arguments()))["task_id"]
    service = world.rt.chat_service()
    sent = []

    async def scripted_send(session_id, text, **kwargs):
        session = service.store.get_session(session_id)
        sent.append(session.runtime or "jarvis")
        assert kwargs == {"direct_user": False, "routine_run": True}
        for event in [
            {"kind": "tool_call", "payload": {"name": "read", "turn_id": "run"}},
            {"kind": "tool_result", "payload": {"output": "checked", "turn_id": "run"}},
            {
                "kind": "assistant_text",
                "payload": {"text": "Checked and compared the project.", "turn_id": "run"},
            },
            {"kind": "turn_finished", "payload": {"status": "done", "turn_id": "run"}},
        ]:
            for queue in service._subs.get(session_id, ()):
                queue.put_nowait(event)
        return "run"

    monkeypatch.setattr(service, "send", scripted_send)

    async def owned(task_id, tags, prompt, cancel):
        return await run_owned_routine(world.rt, task_id, tags, prompt, cancel)

    runner = TaskRunner(
        store=world.tasks,
        bus=EventBus(),
        harness_manager=None,
        tts=None,
        tool_executor=None,
        tool_registry={},
        owned_agent_runner=owned,
    )
    world.scheduler.attach_runner(runner)
    if paused:
        await world.scheduler.pause(tid)
    await world.scheduler.run_now(tid)
    await asyncio.gather(*world.scheduler._runner_tasks)
    assert (await world.tasks.get(tid))["state"] == "completed"
    result = service.notices[-1][1]["text"]
    assert result == "Checked and compared the project."
    assert sent == [world.runtime_name]
    notice = service.notices[-1]
    assert notice[0] == "society:scout" and notice[1]["kind"] == "society_result"
    assert notice[1]["task_id"] == tid and notice[1]["text"] == result


async def test_unregistered_promise_continues_then_fails_honestly(world):
    events = []

    async def emit(event):
        events.append(event)

    service = SimpleNamespace(undelivered_questions=lambda *_: [], pending_approvals=lambda *_: [])
    handle = SimpleNamespace(
        session=world.store.get_session("society:scout"),
        turn_id="t1",
        cancel=asyncio.Event(),
        emit=emit,
    )
    gate = TurnCompletion(service, handle, arguments()["request_quote"], allow_correction=True)
    promise = {
        "kind": "assistant_text",
        "payload": {
            "text": "The current version is available. I'll check again later and report back.",
        },
    }
    await gate.emit(promise)
    assert events[-1]["payload"]["text"] == "The current version is available."
    await gate.emit({"kind": "turn_finished", "payload": {"status": "done"}})
    assert "register" in await gate.next_prompt()
    await gate.emit(promise)
    await gate.emit({"kind": "turn_finished", "payload": {"status": "done"}})
    assert await gate.next_prompt() is None
    await gate.publish()
    assert events[-1]["payload"]["status"] == "error"
    assert await world.tasks.list() == []  # The prose guard never schedules work itself.


async def test_promise_needs_a_real_active_receipt_not_an_unrelated_tool(world):
    result = await submit(world, arguments())
    events = []

    async def emit(event):
        events.append(event)

    service = SimpleNamespace(undelivered_questions=lambda *_: [], pending_approvals=lambda *_: [])
    handle = SimpleNamespace(
        session=world.store.get_session("society:scout"),
        turn_id="t1",
        cancel=asyncio.Event(),
        emit=emit,
    )
    gate = TurnCompletion(service, handle, arguments()["request_quote"], allow_correction=True)
    await gate.emit(
        {
            "kind": "tool_call",
            "payload": {
                "call_id": "saved",
                "name": "mcp__jarvis__society_propose_change",
                "input": arguments(),
            },
        }
    )
    await gate.emit(
        {
            "kind": "tool_result",
            "payload": {
                "call_id": "saved",
                "output": json.dumps(result),
                "is_error": False,
            },
        }
    )
    promise = {"kind": "assistant_text", "payload": {"text": "I'll check again later."}}
    await gate.emit(promise)
    assert events[-1] == promise and not gate.background_unregistered
    await world.scheduler.cancel_task(result["task_id"])
    await gate.emit(promise)
    assert gate.background_unregistered


async def test_delete_retains_cancellation_receipt(world):
    original = arguments()
    tid = (await submit(world, original))["task_id"]
    result = await submit(world, arguments("Delete that task", operation="delete", task_id=tid))
    assert result["state"] == "cancelled"
    again = await submit(world, original)
    assert again["reused"] and again["state"] == "cancelled"


async def test_concurrent_identical_calls_register_only_one_job(world):
    args = arguments()
    origin = ChatTurn("society:scout", "t1", args["request_quote"], True, "trace")
    token = current_chat_turn.set(origin)
    registration = register_turn(origin.session_id)
    current_chat_turn.reset(token)
    try:
        responses = await asyncio.gather(
            call(world, "society_propose_change", args),
            call(world, "society_propose_change", args),
        )
    finally:
        unregister_turn(origin.session_id, registration)
    decoded = [json.loads(response) for response in responses]
    assert all(r["applied"] for r in decoded)
    assert len({r["task_id"] for r in decoded}) == 1
    assert len(await world.tasks.list()) == 1


async def test_waiting_recurring_task_is_not_restarted_by_a_stale_heap_entry(world):
    tid = (await submit(world, arguments(schedule={"kind": "every", "interval_seconds": 60})))[
        "task_id"
    ]
    calls = []

    async def run(task_id, *_args, **_kwargs):
        calls.append(task_id)

    world.scheduler.attach_runner(SimpleNamespace(run=run))
    await world.tasks.update_state(tid, "paused", error="Waiting for a subscription")
    world.clock.now_ns += 60_000_000_000
    await world.scheduler._drain_due_tasks(world.clock.now_ns)
    await world.scheduler._safe_run(tid)
    assert calls == [] and tid not in world.scheduler._known


async def test_permission_ceiling_prevents_autonomous_creation(world):
    await world.rt.roster.update("scout", {"permission_ceiling": "safe"})
    result = await submit(world, arguments())
    assert not result.get("applied"), result
    assert await world.tasks.list() == []


async def test_provider_error_does_not_fall_back_or_claim_completion(world, monkeypatch):
    tid = (await submit(world, arguments()))["task_id"]
    calls = []

    async def unavailable(*_args, **_kwargs):
        calls.append("provider")
        raise RuntimeError("Subscription temporarily unavailable")

    monkeypatch.setattr(world.rt.chat_service(), "send", unavailable)

    async def owned(task_id, tags, prompt, cancel):
        return await run_owned_routine(world.rt, task_id, tags, prompt, cancel)

    runner = TaskRunner(
        store=world.tasks,
        bus=EventBus(),
        harness_manager=None,
        tts=None,
        tool_executor=None,
        tool_registry={},
        owned_agent_runner=owned,
    )
    await runner.run(tid)
    row = await world.tasks.get(tid)
    assert row["state"] == "failed" and "unavailable" in row["last_error"]
    assert calls == ["provider"]
    assert not any(n.get("status") == "done" for _, n in world.rt.chat_service().notices)
