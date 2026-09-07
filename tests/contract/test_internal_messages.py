"""Internal messaging contracts: durable delivery, trusted authors and replay."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.runner_api import messages_from_events
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.plugins.tool.message_agent import LeadMessageAgentTool
from jarvis.society.agent_tools import MessageAgentTool
from jarvis.society.chat_binding import make_deliver_hook
from jarvis.society.delivery import IncomingMessage, incoming_context
from jarvis.society.runtime import SocietyRuntime


@pytest.fixture
async def world(tmp_path, monkeypatch):
    import jarvis.agent_chat.service as service_module

    seen = []

    async def run(handle, prompt, **kwargs):
        seen.append((handle.session.session_id, prompt, incoming_context.get()))
        await handle.cancel.wait()
        await handle.emit(
            make_event(
                "turn_finished",
                {
                    "turn_id": handle.turn_id,
                    "status": "cancelled",
                },
            )
        )

    monkeypatch.setattr(service_module, "run_brain_turn", run)
    store = AgentChatStore(tmp_path / "chat.db")
    svc = AgentChatService(store)
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path)))
    rt = SocietyRuntime(tmp_path, seed_starter_team=False, chat_service=lambda: svc)
    await rt.ensure_started()
    await rt.roster.create(name="Gmail agent", provider="openai", model="test-model")
    await rt.roster.create(name="Scout", provider="openai", model="test-model")
    rt.set_deliver(
        make_deliver_hook(
            lambda: svc,
            lambda: cfg,
            resolve_name=lambda ident: "Jarvis" if ident == rt.lead_id else ident.title(),
        )
    )
    try:
        yield rt, svc, seen
    finally:
        await rt.close()
        await svc.cancel_all()
        store.close()


CTX = SimpleNamespace(trace_id=uuid4(), user_utterance="", config={})


async def drain(rt):
    for _ in range(5):
        await rt.scheduler.drain_deliveries()
        await asyncio.sleep(0.01)


async def test_lead_message_is_an_internal_chat_receipt(world):
    rt, svc, seen = world
    tool = LeadMessageAgentTool(runtime_resolver=lambda: rt)
    result = await tool.execute({"target": "Gmail agent", "text": "A test message"}, CTX)
    await drain(rt)
    assert result.success and tool.risk_tier == "safe"
    target = await rt.roster.resolve("Gmail agent")
    events = svc.store.list_events(target.session_id)
    receipt = next(e["payload"] for e in events if e["kind"] == "agent_message")
    assert receipt["sender_name"] == "Jarvis" and receipt["sender_kind"] == "jarvis"
    assert receipt["text"] == "A test message"
    assert not any(e["kind"] == "user_message" for e in events)
    assert seen[0][2].message_id == receipt["message_id"]
    assert await rt.store.delivery_status(receipt["message_id"]) == "delivered"


async def test_busy_recipient_keeps_fifo_receipts_then_resumes_once(world):
    rt, svc, _ = world
    first = await rt.say(from_agent=rt.lead_id, to_agent="scout", text="first")
    await drain(rt)
    second = await rt.say(from_agent=rt.lead_id, to_agent="scout", text="second")
    third = await rt.say(from_agent=rt.lead_id, to_agent="scout", text="third")
    await drain(rt)
    assert await rt.store.delivery_status(second.event_id) == "queued"
    receipt = svc.store.incoming_message("society:scout", second.event_id)
    assert receipt["status"] == "queued"
    await svc.cancel("society:scout")
    await drain(rt)
    assert await rt.store.delivery_status(second.event_id) == "delivered"
    assert await rt.store.delivery_status(third.event_id) == "queued"
    await rt.scheduler.on_envelope(first)
    await svc.cancel("society:scout")
    await drain(rt)
    events = svc.store.list_events("society:scout")
    assert [e["payload"]["text"] for e in events if e["kind"] == "agent_message"] == [
        "first",
        "second",
        "third",
    ]
    assert len([e for e in events if e["kind"] == "turn_started"]) == 3


async def test_replay_after_chat_acceptance_does_not_start_another_turn(world):
    rt, svc, _ = world
    env = await rt.say(from_agent=rt.lead_id, to_agent="scout", text="once")
    await drain(rt)
    # Simulate interruption after chat accepted the turn but before the board
    # stored the receipt. The receiving chat is the idempotency boundary.
    await rt.store.mark_delivery(env.event_id, "queued")
    await svc.cancel("society:scout")
    await drain(rt)
    events = svc.store.list_events("society:scout")
    assert sum(e["kind"] == "turn_started" for e in events) == 1
    assert sum(e["kind"] == "agent_message" for e in events) == 1


async def test_queue_survives_runtime_restart(world, tmp_path):
    rt, svc, _ = world
    rt.set_deliver(None)
    env = await rt.say(from_agent=rt.lead_id, to_agent="scout", text="recover me")
    await rt.close()
    await rt.ensure_started()
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path)))
    rt.set_deliver(make_deliver_hook(lambda: svc, lambda: cfg))
    await drain(rt)
    assert await rt.store.delivery_status(env.event_id) == "delivered"


async def test_failure_updates_a_visible_queued_message(world):
    rt, svc, _ = world
    await rt.say(from_agent=rt.lead_id, to_agent="scout", text="busy")
    await drain(rt)
    env = await rt.say(from_agent=rt.lead_id, to_agent="scout", text="waiting")
    await drain(rt)
    await rt.store.set_kill_switch(True)
    await drain(rt)
    assert await rt.store.delivery_status(env.event_id) == "failed"
    assert svc.store.incoming_message("society:scout", env.event_id)["status"] == "failed"


async def test_agent_reply_keeps_conversation_and_cannot_forge_sender(world):
    rt, _, _ = world
    rt.set_deliver(None)
    tool = MessageAgentTool(rt, "scout")
    incoming = IncomingMessage(
        message_id="parent",
        sender_id=rt.lead_id,
        sender_name="Jarvis",
        sender_kind="jarvis",
        text="hello",
        prompt="hello",
        trace_id="conversation",
    )
    token = incoming_context.set(incoming)
    try:
        result = await tool.execute(
            {
                "target": "Gmail agent",
                "text": "reply",
                "kind": "answer",
                "sender": "Jarvis",
            },
            CTX,
        )
    finally:
        incoming_context.reset(token)
    events = await rt.store.events_for_trace(result.output["trace_id"])
    assert events[0].from_agent == "scout"
    assert events[0].parent_event_id == "parent"
    assert result.output["trace_id"] == "conversation"
    first = await tool.execute({"target": "Gmail agent", "text": "new"}, CTX)
    second = await tool.execute({"target": "Gmail agent", "text": "another"}, CTX)
    assert first.output["trace_id"] != second.output["trace_id"]


async def test_concurrent_lazy_start_opens_once(tmp_path):
    rt = SocietyRuntime(tmp_path, seed_starter_team=False)
    try:
        await asyncio.gather(*(rt.ensure_started() for _ in range(10)))
        before = rt.store.bus.active_subs
        conn = rt.store.conn
        await rt.ensure_started()
        assert rt.store.conn is conn
        assert rt.store.bus.active_subs == before
        assert len(await rt.roster.list()) == 1
    finally:
        await rt.close()


def test_model_history_reads_delivered_internal_messages_once():
    message = IncomingMessage(
        message_id="m1",
        sender_id="scout",
        sender_name="Scout",
        sender_kind="agent",
        text="hello",
        prompt="[say from Scout]\nhello",
        trace_id="t",
    ).model_dump()
    queued = make_event("agent_message", message)
    delivered = make_event("agent_message_status", {"message_id": "m1", "status": "delivered"})
    assert messages_from_events([queued]) == []
    history = messages_from_events([queued, delivered, delivered])
    assert len(history) == 1 and history[0].content == message["prompt"]


def test_delivery_status_parity():
    root = Path(__file__).resolve().parents[2]
    schema = (root / "jarvis/society/society_schema.sql").read_text(encoding="utf-8")
    ts = (root / "jarvis/ui/web/frontend/src/lib/agentChatApi.ts").read_text(encoding="utf-8")
    choices = IncomingMessage.model_fields["status"].annotation.__args__
    assert choices == ("queued", "delivered", "failed")
    assert "CHECK (status IN ('queued', 'delivered', 'failed'))" in schema
    assert '["queued", "delivered", "failed"] as const' in ts


async def test_internal_send_uses_executor_without_external_action_approval(world):
    from tests.unit.safety.test_tool_executor_voice_confirm import _executor

    rt, _, _ = world
    executor, approval, _ = _executor()
    result = await executor.execute(
        LeadMessageAgentTool(runtime_resolver=lambda: rt),
        args={"target": "Scout", "text": "hello"},
        user_utterance="Send Scout a message",
        config_snapshot={"voice_confirm": True},
    )
    assert result.success
    assert approval.wait_calls == 0


async def test_explicit_sender_deny_and_paused_recipient_are_not_bypassed(world):
    rt, _, _ = world
    await rt.roster.update("scout", {"denies": ["society_message_agent"]})
    denied = await MessageAgentTool(rt, "scout").execute(
        {"target": "Gmail agent", "text": "hello"}, CTX
    )
    assert not denied.success and denied.output["reason"] == "blocked_by_policy"
    await rt.roster.update("scout", {"state": "paused"})
    paused = await LeadMessageAgentTool(runtime_resolver=lambda: rt).execute(
        {"target": "Scout", "text": "hello"}, CTX
    )
    assert not paused.success and paused.output["reason"] == "target_paused"
