"""Real delegation lifecycle through the speech inbox, with fake providers only."""

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.agent_chat.runner_api import messages_from_events
from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested, DelegationResultReady
from jarvis.plugins.tool.delegate_to_agent import DelegateToAgentTool
from jarvis.society.events import MsgType
from jarvis.speech.pipeline import TurnTakingState
from jarvis.ui.web.server import WebServer
from tests.contract import test_society_communication as communication
from tests.unit.speech.test_realtime_announcement_bridge import _pipeline

world = communication.world


@pytest.mark.asyncio
async def test_two_delegations_return_after_completion_without_blocking_conversation(
    world, monkeypatch,
):
    runtime, chat, _ = world
    pipeline, tts, _, live = _pipeline(accepted=True)
    bus = EventBus()
    bus.subscribe(AnnouncementRequested, pipeline._on_announcement)
    runtime._publish_event = bus.publish
    monkeypatch.setattr("jarvis.core.delegation.BATCH_WINDOW_S", 0.005)
    tool = DelegateToAgentTool(runtime_resolver=lambda: runtime)
    receipts = []
    for agent in ("Scout", "Archivist"):
        receipt = await asyncio.wait_for(tool.execute(
            {"agent": agent, "task": f"Research {agent}", "reply_policy": "always"},
            communication.ctx(),
        ), timeout=1)
        assert receipt.success and receipt.output["state"] == "running"
        receipts.append(receipt)
    assert not live.calls  # Dispatch is acknowledged before either agent has finished.
    pipeline._turn_state = TurnTakingState.USER_SPEAKING
    for name in ("scout", "archivist"):
        await chat.queues[f"society:{name}"].put({
            "kind": "assistant_text", "payload": {"text": f"Recorded {name} findings."},
        })
        await chat.queues[f"society:{name}"].put({
            "kind": "turn_finished", "payload": {"status": "completed"},
        })
    await asyncio.wait_for(asyncio.gather(*list(runtime._watchers)), timeout=3)
    assert not live.calls
    assert len(chat.notices) == 2
    await pipeline._set_turn_state(TurnTakingState.LISTENING)
    await asyncio.sleep(0.04)
    assert len(live.calls) == 1 and not tts.calls
    for receipt in receipts:
        assert receipt.output["assignment_id"] in live.calls[0]["report"]
    assert "scout findings" in live.calls[0]["report"]
    assert "archivist findings" in live.calls[0]["report"]


@pytest.mark.asyncio
async def test_agent_question_is_returned_and_turn_end_does_not_claim_success(world):
    runtime, chat, published = world
    receipt = await DelegateToAgentTool(runtime_resolver=lambda: runtime).execute(
        {"agent": "Scout", "task": "Research it", "reply_policy": "on_error"},
        communication.ctx(),
    )
    request = await runtime.store.get_event(receipt.output["assignment_id"])
    await runtime.say(
        from_agent="scout", to_agent="jarvis", msg_type=MsgType.QUERY,
        text="Which repository do you mean?", trace_id=request.trace_id,
        parent_event_id=request.event_id,
    )
    await chat.queues["society:scout"].put({
        "kind": "turn_finished", "payload": {"status": "completed"},
    })
    await asyncio.wait_for(asyncio.gather(*list(runtime._watchers)), timeout=3)
    assert len(published) == 1
    assert "needs_input" in published[0].report
    assert len(chat.notices) == 1
    results = [e for e in await runtime.store.events_for_trace(request.trace_id)
               if e.msg_type is MsgType.RESULT]
    assert results[0].payload["status"] == "blocked"


@pytest.mark.asyncio
async def test_coding_result_survives_chat_event_storage_and_enters_followup_context(world):
    _, chat, _ = world
    original = chat.store.list_sessions(surface="jarvis")[0]
    notices = []

    async def post(session_id, payload):
        notices.append((session_id, payload))
        await chat._emit(session_id, {"kind": "notice", "payload": payload})

    chat.post_notice = post
    server = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent_chat=chat)))
    event = DelegationResultReady(
        session_id=original.session_id, request_id="task-1", agent_name="T1",
        status="completed", text="T1 reports: Fixed login.",
        report='{"request":"Fix login", "report":"Tests passed; deployment pending"}',
    )
    await WebServer._forward_delegation_to_chat(server, event)
    assert notices[0][0] == original.session_id
    messages = messages_from_events([{"kind": "notice", "payload": notices[0][1]}])
    assert "deployment pending" in messages[0].content
    assert "Fix login" in messages[0].content


@pytest.mark.asyncio
async def test_chat_factory_installs_one_lazy_result_bridge(tmp_path):
    server = WebServer.__new__(WebServer)
    server.cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path)))
    server.bus = EventBus()
    server.app = SimpleNamespace(state=SimpleNamespace(agent_chat=None))
    first = server._build_agent_chat_service()
    first.store.close()
    service = server._build_agent_chat_service()
    server.app.state.agent_chat = service
    session = service.store.create_session(
        provider="openai", model="", effort="", cwd=str(tmp_path), surface="jarvis",
    )
    queue = service.subscribe(session.session_id)
    try:
        await server.bus.publish(DelegationResultReady(
            session_id=session.session_id, request_id="job", agent_name="T1",
            status="completed", text="Reported outcome", report="Task and evidence",
        ))
        assert queue.qsize() == 1
        notice = queue.get_nowait()
        assert notice["kind"] == "notice"
        assert notice["payload"]["report"] == "Task and evidence"
    finally:
        service.unsubscribe(session.session_id, queue)
        service.store.close()
