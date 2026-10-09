"""Subscription orchestration preserves tool authority, billing and audio lifetime."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from jarvis.core.events import BrainTurnCompleted
from jarvis.live.config import LiveConfig
from jarvis.live.state import LiveLedger
from jarvis.live.tools import LiveTools
from tests.fakes.fake_subscription_session import (
    ApprovalSubscriptionGateway,
    AppshotSubscriptionGateway,
    PausingAgentGateway,
    PausingSubscriptionReasoning,
    ScriptedSubscriptionReasoning,
    SubscriptionConnection,
    SubscriptionEvents,
    SubscriptionGateway,
    completed_response,
    spoken_result,
    subscription_provider,
)


@pytest.fixture
def make_session(monkeypatch, tmp_path):
    import jarvis.live.subscription as module

    sessions = []

    def make(rounds, gateway=None):
        reasoning = ScriptedSubscriptionReasoning(rounds)
        monkeypatch.setattr(module, "SubscriptionReasoning", lambda **kwargs: reasoning)
        config = SimpleNamespace(
            live=LiveConfig(
                configured=True,
                auth_mode="chatgpt_subscription",
                backend_model="retained-api-model",
                voice="gleam",
                subscription_backend_model="subscription-model",
                subscription_voice="cove",
            ),
            brain=SimpleNamespace(reply_language="en"),
        )
        messages = []

        async def send(value):
            messages.append(value)

        bus = SubscriptionEvents()
        session = module.SubscriptionLiveVoiceSession(
            session_id=f"subscription-{len(sessions)}",
            providers=[subscription_provider()],
            config=config,
            send_binary=send,
            send_json=send,
            bus=bus,
        )
        ledger = LiveLedger(tmp_path / f"{session.session_id}.sqlite3")
        gateway = gateway or SubscriptionGateway()
        session._ledger = ledger
        session._connection = SubscriptionConnection()
        session._tools = LiveTools(
            gateway,
            ledger,
            session.session_id,
            language="en",
            backend_model="subscription-model",
            model_selection=session._tool_model_selection,
        )
        session._tools.user_text = "Inspect the application state."
        sessions.append(session)
        return session, reasoning, gateway, messages, bus, config

    yield make
    for session in sessions:
        session._cancel_report_timeout()
        if not session._ended:
            session._ledger.close()


async def wait_for_jobs(session):
    if session._jobs:
        await asyncio.gather(*tuple(session._jobs))
    await asyncio.sleep(0)


async def delegate_input(session, identifier, prompt):
    timestamp = max(session._last_end.values()) + 1
    await session._event({
        "type": "session.input_transcript.done", "segment_id": identifier,
        "transcript": prompt, "snapshot": True, "is_final": True,
        "start_ms": timestamp, "end_ms": timestamp,
    })
    await session._event({
        "type": "session.delegation.created",
        "delegation": {"id": identifier},
        "prompt": prompt,
    })


async def test_followup_preempts_only_reasoning_and_retains_every_request(make_session):
    session, _, gateway, _, _, _ = make_session([])
    reasoning = PausingSubscriptionReasoning([
        completed_response("old", [spoken_result("Obsolete answer")]),
        completed_response("new", [spoken_result("Both requests accepted")]),
    ])
    session._reasoning = reasoning
    original = "Start one Codex session for project Alpha."
    followup = "Also send the agent the test requirements."
    try:
        await delegate_input(session, "first", original)
        await reasoning.entered[0].wait()
        await delegate_input(session, "followup", followup)
        await session._event({
            "type": "session.delegation.created", "delegation": {"id": "followup"},
            "prompt": followup,
        })
        await asyncio.wait_for(reasoning.entered[1].wait(), 1)
        await wait_for_jobs(session)
        request = json.dumps(reasoning.requests[1]["input"])
        assert original in request and followup in request
        assert reasoning.cancelled == [0]
        assert not gateway.calls
        replies = [m for m in session._connection.sent if m["type"] == "session.commentary.append"]
        assert [m["content"] for m in replies] == ["Both requests accepted"]
        assert replies[0]["delegation_id"] == "followup"
        assert not session._tools.cancel_token.is_cancelled()
    finally:
        await session.handle_control({"type": "cancel_work"})


def agent_call(identifier, name, args):
    return {
        "id": identifier, "type": "function_call", "call_id": identifier,
        "name": "call_tool",
        "arguments": json.dumps({"name": name, "arguments_json": json.dumps(args)}),
    }


@pytest.mark.parametrize("uncertain", [False, True])
async def test_followup_waits_for_tool_receipt_without_cancelling_or_repeating_agent(
    make_session, uncertain,
):
    gateway = PausingAgentGateway(uncertain=uncertain)
    start = {"project": "Alpha", "count": 1}
    session, reasoning, _, _, _, _ = make_session([
        completed_response("start", [
            agent_call("start-1", "start-agent", start),
            agent_call("obsolete-send", "message-agent", {"message": "Old instructions"}),
        ]),
        completed_response("followup", [
            agent_call("start-replanned", "start-agent", start),
            *([] if uncertain else [agent_call("send-1", "message-agent", {
                "agent_id": "agent-alpha", "message": "Run the regression tests.",
            })]),
        ]),
        completed_response("done", [spoken_result(
            "The startup is unconfirmed." if uncertain else "The message was accepted."
        )]),
    ], gateway)
    try:
        await session.handle_control({"type": "text_input", "text": "Start one agent for Alpha."})
        await asyncio.wait_for(gateway.started.wait(), 1)
        token = gateway.requests[0].cancel_token
        await session.handle_control({
            "type": "text_input", "text": "Tell that agent to run the regression tests.",
        })
        assert not token.is_cancelled()
        assert len(reasoning.requests) == 1
        assert any(m["type"] == "session.thinking.append" for m in session._connection.sent)
        gateway.release.set()
        await wait_for_jobs(session)
        assert [c[0] for c in gateway.calls] == (
            ["start-agent"] if uncertain else ["start-agent", "message-agent"]
        )
        if not uncertain:
            assert gateway.calls[-1][1]["agent_id"] == "agent-alpha"
        assert not token.is_cancelled()
        inputs = reasoning.requests[1]["input"]
        receipts = {
            i["call_id"]: json.loads(i["output"])
            for i in inputs if i.get("type") == "function_call_output"
        }
        assert receipts["obsolete-send"]["executed"] is False
        if not uncertain:
            assert receipts["start-1"]["output"]["agent_id"] == "agent-alpha"
        assert "Start one agent for Alpha." in json.dumps(inputs)
        assert "Tell that agent" in json.dumps(inputs)
        reused = [i for i in reasoning.requests[2]["input"]
                  if i.get("call_id") == "start-replanned" and i["type"] == "function_call_output"]
        assert json.loads(reused[0]["output"])["reused_receipt"]
        assert session._ledger.operation(session.session_id, "start-1") is not None
        assert session._ledger.operation(session.session_id, "start-replanned")["reused_receipt"]
    finally:
        gateway.release.set()
        await session.handle_control({"type": "cancel_work"})


async def test_rapid_followups_keep_requests_and_final_one_session_correction(make_session):
    session, _, _, _, _, _ = make_session([])
    session._reasoning = reasoning = PausingSubscriptionReasoning([
        completed_response("obsolete", [spoken_result("Two sessions started")]),
        completed_response("current", [spoken_result("One session requested")]),
    ])
    try:
        await delegate_input(session, "initial", "Start two Codex sessions.")
        await reasoning.entered[0].wait()
        await delegate_input(session, "message", "Send the test plan to the agent.")
        await delegate_input(session, "correction", "Actually start only one Codex session.")
        await wait_for_jobs(session)
        user_texts = [
            part["text"] for item in reasoning.requests[-1]["input"]
            if item.get("role") == "user"
            for part in item["content"] if part.get("type") == "input_text"
        ]
        assert "Start two Codex sessions." in user_texts
        assert "Send the test plan to the agent." in user_texts
        assert user_texts[-1] == "Actually start only one Codex session."
        assert session._connection.sent[-1]["delegation_id"] == "correction"
        assert len(reasoning.requests) == 2
    finally:
        await session.handle_control({"type": "cancel_work"})


async def test_partial_tool_proposal_is_discarded_when_followup_interrupts_reasoning(make_session):
    session, _, gateway, _, _, _ = make_session([])
    proposal_seen = asyncio.Event()

    class ProposedButUnfinished(PausingSubscriptionReasoning):
        async def stream(self, **request):
            if not self.requests:
                self.requests.append(request)
                yield {"type": "response.output_item.done", "item": agent_call(
                    "uncommitted", "start-agent", {"count": 2},
                )}
                proposal_seen.set()
                await asyncio.Event().wait()
            else:
                async for event in super().stream(**request):
                    yield event

    session._reasoning = reasoning = ProposedButUnfinished([
        [], completed_response("current", [spoken_result("Correction accepted")]),
    ])
    try:
        await delegate_input(session, "old", "Start two sessions.")
        await asyncio.wait_for(proposal_seen.wait(), 1)
        await delegate_input(session, "new", "Start only one session.")
        await asyncio.wait_for(wait_for_jobs(session), 1)
        assert not gateway.calls
        assert "uncommitted" not in json.dumps(reasoning.requests[-1]["input"])
    finally:
        await session.handle_control({"type": "cancel_work"})


@pytest.mark.parametrize("hangup_pending", [False, True])
async def test_followup_yes_keeps_action_approval_and_hangup_consent_separate(
    make_session, hangup_pending,
):
    gateway = ApprovalSubscriptionGateway()
    session, _, _, _, _, _ = make_session([], gateway)
    runtime = session._tools
    pending = await runtime.execute("pending", "inspect-state", {}, 0)
    approval_id = pending["approval_id"]
    if hangup_pending:
        async def ask(_question):
            pass

        runtime.ask_hangup = ask
        runtime.user_text = "Hang up"
        await runtime.execute("hangup-question", "end_call", {}, 0)
    session._reasoning = reasoning = PausingSubscriptionReasoning([
        completed_response("waiting", [spoken_result("Please confirm")]),
        completed_response("approval", [{
            "id": "approval", "type": "function_call", "call_id": "confirm-followup",
            "name": "confirm_action", "arguments": json.dumps({"approval_id": approval_id}),
        }]),
        completed_response("reply", [spoken_result("Confirmation processed")]),
    ])
    try:
        # Start the pending response without inventing another user utterance.
        job = asyncio.create_task(session._run_client_delegation("waiting", "Await confirmation"))
        session._jobs.add(job)
        job.add_done_callback(session._job_finished)
        await reasoning.entered[0].wait()
        await delegate_input(session, "answer", "Yes")
        await asyncio.wait_for(wait_for_jobs(session), 1)
        assert len(gateway.confirmed) == (0 if hangup_pending else 1)
        assert not runtime.end_requested
        if hangup_pending:
            assert approval_id in runtime._pending
            outputs = [i for i in reasoning.requests[-1]["input"]
                       if i.get("type") == "function_call_output"]
            assert "hang-up question" in json.loads(outputs[-1]["output"])["error"]
    finally:
        await session.handle_control({"type": "cancel_work"})


async def test_explicit_cancel_does_not_resume_old_request_on_next_input(make_session):
    session, _, _, _, _, _ = make_session([])
    session._reasoning = reasoning = PausingSubscriptionReasoning([
        completed_response("old", [spoken_result("Old response")]),
        completed_response("new", [spoken_result("New response")]),
    ])
    await delegate_input(session, "old", "Send the old message.")
    await reasoning.entered[0].wait()
    await session.handle_control({"type": "cancel_work"})
    await delegate_input(session, "new", "Tell me the time instead.")
    await asyncio.wait_for(wait_for_jobs(session), 1)
    assert "user cancelled unfinished voice requests" in json.dumps(reasoning.requests[-1]["input"])
    assert not session._queued_requests and not session._unfinished_groups


async def test_followup_retains_original_request_and_receipts_beyond_history_window(make_session):
    session, _, _, _, _, _ = make_session([])
    rounds = [completed_response(f"read-{i}", [agent_call(
        f"read-call-{i}", "inspect-state", {},
    )]) for i in range(18)]
    rounds.extend([
        completed_response("interrupted", [spoken_result("Old answer")]),
        completed_response("current", [spoken_result("New answer")]),
    ])
    session._reasoning = reasoning = PausingSubscriptionReasoning(rounds, paused=(18,))
    try:
        await delegate_input(session, "original", "Inspect Alpha before messaging its agent.")
        await asyncio.wait_for(reasoning.entered[18].wait(), 2)
        await delegate_input(session, "new", "Also include the test requirements.")
        await asyncio.wait_for(wait_for_jobs(session), 2)
        items = reasoning.requests[-1]["input"]
        assert "Inspect Alpha before messaging its agent." in json.dumps(items)
        assert any(i.get("call_id") == "read-call-0" and i["type"] == "function_call_output"
                   for i in items)
        assert not session._unfinished_groups
    finally:
        await session.handle_control({"type": "cancel_work"})


async def test_new_request_after_completed_reply_can_intentionally_repeat_action(make_session):
    gateway = PausingAgentGateway()
    gateway.release.set()
    session, _, _, _, _, _ = make_session([
        completed_response("first", [agent_call("start-1", "start-agent", {"count": 1})]),
        completed_response("first-done", [spoken_result("Started one agent")]),
        completed_response("again", [agent_call("start-2", "start-agent", {"count": 1})]),
        completed_response("again-done", [spoken_result("Started another agent")]),
    ], gateway)
    await delegate_input(session, "first", "Start one agent.")
    await wait_for_jobs(session)
    await delegate_input(session, "again", "Start another agent with the same task.")
    await wait_for_jobs(session)
    assert len(gateway.calls) == 2


async def test_followup_after_approval_reuses_effect_not_the_confirmation(make_session):
    gateway = ApprovalSubscriptionGateway()
    session, _, _, _, _, _ = make_session([], gateway)
    pending = await session._tools.execute("pending", "inspect-state", {}, 0)
    session._reasoning = reasoning = PausingSubscriptionReasoning([
        completed_response("confirm", [{
            "id": "confirm", "type": "function_call", "call_id": "confirm",
            "name": "confirm_action",
            "arguments": json.dumps({"approval_id": pending["approval_id"]}),
        }]),
        completed_response("interrupted", [spoken_result("Action completed")]),
        completed_response("replan", [agent_call("repeat", "inspect-state", {})]),
        completed_response("result", [spoken_result("The previous action already completed")]),
    ], paused=(1,))
    try:
        await delegate_input(session, "approve", "Yes")
        await asyncio.wait_for(reasoning.entered[1].wait(), 1)
        await delegate_input(session, "followup", "Also tell me what happened.")
        await asyncio.wait_for(wait_for_jobs(session), 1)
        assert len(gateway.confirmed) == 1 and len(gateway.calls) == 1
        assert session._ledger.operation(session.session_id, "repeat")["reused_receipt"]
        assert not session._tools._pending
    finally:
        await session.handle_control({"type": "cancel_work"})


@pytest.mark.asyncio
async def test_same_delegation_runs_one_jarvis_action_and_keeps_subscription(make_session):
    tool = {
        "id": "item-1",
        "type": "function_call",
        "call_id": "call-1",
        "name": "call_tool",
        "arguments": json.dumps(
            {
                "name": "inspect-state",
                "arguments_json": "{}",
            }
        ),
    }
    session, reasoning, gateway, _, bus, config = make_session(
        [
            completed_response("r1", [tool]),
            completed_response("r2", [spoken_result()]),
        ]
    )
    event = {
        "type": "session.delegation.created",
        "delegation": {"id": "d1"},
        "prompt": "Inspect the application state.",
    }
    await session._event(event)
    await session._event(event)
    await wait_for_jobs(session)
    assert len(gateway.calls) == 1
    assert gateway.calls[0][2].provider == "openai-chatgpt-subscription"
    assert gateway.calls[0][2].brain_override is not None
    assert len(reasoning.requests) == 2
    assert all(t["type"] == "function" for t in reasoning.requests[0]["tools"])
    assert config.live.backend_model == "retained-api-model"
    assert config.live.voice == "gleam"
    assert session._connection.sent[-1]["content"] == "The state is verified."
    assert session._connection.sent[-1]["delegation_id"] == "d1"
    assert all(e.cost_usd == 0 for e in bus.events if isinstance(e, BrainTurnCompleted))


@pytest.mark.asyncio
async def test_failed_stream_never_executes_announced_tool(make_session):
    tool = {
        "id": "item-1",
        "type": "function_call",
        "call_id": "call-1",
        "name": "call_tool",
        "arguments": "{}",
    }
    session, _, gateway, messages, _, _ = make_session(
        [
            [
                {"type": "response.output_item.done", "item": tool},
                {"type": "response.failed", "response": {"id": "failed"}},
            ]
        ]
    )
    await session._delegate("d1", "Inspect state")
    assert not gateway.calls
    assert any(m.get("code") == "subscription_unavailable" for m in messages)
    assert not session._connection.closed


@pytest.mark.asyncio
async def test_typed_input_uses_client_delegation_without_api_commands(make_session):
    session, _, _, _, _, _ = make_session([completed_response("r1", [spoken_result()])])
    await session.handle_control({"type": "text_input", "text": "Inspect state"})
    await wait_for_jobs(session)
    assert session._connection.sent[-1]["delegation_id"] is None
    assert not any(e["type"].startswith("response.") for e in session._connection.sent)


@pytest.mark.asyncio
async def test_final_caption_corrects_one_persisted_segment(make_session):
    session, _, _, _, _, _ = make_session([])
    await session._event(
        {
            "type": "session.input_transcript.delta",
            "segment_id": "turn1",
            "transcript": "Set it to five",
            "snapshot": True,
            "start_ms": 10,
            "end_ms": 20,
        }
    )
    await session._event(
        {
            "type": "session.input_transcript.done",
            "segment_id": "turn1",
            "transcript": "Set it to nine",
            "snapshot": True,
            "is_final": True,
            "start_ms": 10,
            "end_ms": 30,
        }
    )
    assert session._ledger.transcript(session.session_id) == [
        {"role": "user", "delta": "Set it to nine", "start_ms": 10, "end_ms": 30},
    ]


@pytest.mark.asyncio
async def test_cancel_stops_inference_without_starting_another_provider(make_session):
    session, reasoning, gateway, _, _, _ = make_session([])
    reasoning.block = asyncio.Event()
    await session._event(
        {
            "type": "session.delegation.created",
            "delegation": {"id": "d1"},
            "prompt": "Inspect state",
        }
    )
    await reasoning.started.wait()
    await session.handle_control({"type": "cancel_work"})
    assert not session._jobs
    assert not gateway.calls
    assert len(reasoning.requests) == 1


@pytest.mark.asyncio
async def test_close_does_not_wait_for_unavailable_public_ack(make_session, monkeypatch):
    session, reasoning, _, _, bus, _ = make_session([])

    async def unexpected_wait(*_args, **_kwargs):
        raise AssertionError("Private voice must not wait for a public API close event")

    monkeypatch.setattr(asyncio, "wait_for", unexpected_wait)
    await session.end()
    assert session._connection.closed
    assert reasoning.closed
    assert all(e.cost_usd == 0 for e in bus.events if isinstance(e, BrainTurnCompleted))


def test_operation_brain_override_cannot_fall_back_to_an_api_key():
    from jarvis.brain.manager import BrainManager
    from jarvis.core.model_selection import ModelSelection, use_operation_model

    manager = BrainManager.__new__(BrainManager)
    subscription_brain = object()
    with use_operation_model(
        ModelSelection("subscription", "chosen", brain_override=subscription_brain)
    ):
        assert manager._get_brain("subscription", "chosen") is subscription_brain
        with pytest.raises(RuntimeError, match="billing account"):
            manager._get_brain("openai", "chosen")
    api_brain = object()
    manager._brain_cache = {("openai", "api-model"): api_brain}
    assert manager._get_brain("openai", "api-model") is api_brain


def test_switching_modes_retains_api_configuration():
    profile = LiveConfig(
        configured=True,
        backend_model="api-model",
        voice="gleam",
        subscription_backend_model="plan-model",
        subscription_voice="cove",
    )
    api_wire = profile.session_config(language="en", tools=[])
    subscription = profile.model_copy(update={"auth_mode": "chatgpt_subscription"})
    wire = subscription.session_config(language="en", tools=[])
    assert wire["model"] == "gpt-live-1-codex"
    assert wire["delegation"] == {"type": "client"}
    assert wire["audio"]["output"]["voice"] == "cove"
    assert (
        subscription.model_copy(update={"auth_mode": "api_key"}).session_config(
            language="en",
            tools=[],
        )
        == api_wire
    )


@pytest.mark.asyncio
async def test_queued_request_cannot_borrow_a_later_corrections_revision(make_session):
    session, reasoning, gateway, _, _, _ = make_session(
        [
            completed_response("r1", [spoken_result("First answer")]),
            completed_response("r2", [spoken_result("Latest answer")]),
        ]
    )
    reasoning.block = asyncio.Event()
    await session._event(
        {
            "type": "session.delegation.created",
            "delegation": {"id": "first"},
            "prompt": "First request",
        }
    )
    await reasoning.started.wait()
    await session._event(
        {
            "type": "session.delegation.created",
            "delegation": {"id": "stale"},
            "prompt": "Delete the document",
        }
    )
    await session._event(
        {
            "type": "session.delegation.created",
            "delegation": {"id": "current"},
            "prompt": "Only inspect the document",
        }
    )
    reasoning.block.set()
    await wait_for_jobs(session)
    assert len(reasoning.requests) == 2
    assert reasoning.requests[1]["input"][-1]["content"][0]["text"] == "Only inspect the document"
    assert not gateway.calls
    assert session._connection.sent[-1]["delegation_id"] == "current"


@pytest.mark.asyncio
async def test_screenshot_tool_result_becomes_a_supported_image_input(make_session):
    tool = {
        "id": "item-1",
        "type": "function_call",
        "call_id": "image-call",
        "name": "call_tool",
        "arguments": json.dumps(
            {
                "name": "inspect-state",
                "arguments_json": "{}",
            }
        ),
    }
    session, reasoning, gateway, _, _, _ = make_session(
        [
            completed_response("r1", [tool]),
            completed_response("r2", [spoken_result()]),
        ]
    )
    gateway.image = {"type": "image", "mime": "image/png", "data": "AAAA"}
    await session._delegate("d1", "Look at the screen")
    image_messages = [
        item
        for item in reasoning.requests[1]["input"]
        if item.get("role") == "user" and isinstance(item.get("content"), list)
    ]
    assert any(
        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"} in item["content"]
        for item in image_messages
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("refused", [False, True])
async def test_new_appshot_request_keeps_old_image_as_context_and_uses_new_tool_result(
    make_session, refused,
):
    gateway = AppshotSubscriptionGateway(refused=refused)
    capture = {
        "id": "fresh-shot", "type": "function_call", "call_id": "capture-1",
        "name": "take_appshot", "arguments": '{"scope":"window"}',
    }
    session, reasoning, _, _, _, _ = make_session([
        completed_response("r1", [capture]),
        completed_response("r2", [spoken_result("Capture refused." if refused else "Captured.")]),
    ], gateway=gateway)
    await session.attach_appshot(b"OLD", "image/png", "An earlier skill editor screenshot")
    request = "Take a new appshot and look at my current screen."
    await session._delegate("fresh-request", request)

    first = reasoning.requests[0]
    assert "take_appshot" in {tool["name"] for tool in first["tools"]}
    assert first["input"][-1]["content"][0]["text"] == request
    previous = first["input"][-2]["content"]
    assert "Earlier screen snapshot" in previous[0]["text"]
    assert previous[-1]["image_url"] == "data:image/png;base64,T0xE"
    assert [(name, args) for name, args, _ in gateway.calls] == [
        ("take_appshot", {"scope": "window"}),
    ]
    followup = reasoning.requests[1]["input"]
    result = next(item for item in followup if item.get("type") == "function_call_output")
    assert json.loads(result["output"])["success"] is not refused
    assert ("data:image/png;base64,TkVX" in json.dumps(followup)) is not refused
    if refused:
        assert "Blocked by the privacy filter" in result["output"]
    else:
        assert session._image_context == [
            {"type": "input_image", "image_url": "data:image/png;base64,TkVX"},
        ]


@pytest.mark.asyncio
async def test_existing_appshot_stays_available_for_a_followup_without_recapture(make_session):
    session, reasoning, gateway, _, _, _ = make_session([
        completed_response("r1", [spoken_result("This image shows a skill editor.")]),
        completed_response("r2", [spoken_result("The button in that image says Save.")]),
    ])
    await session.attach_appshot(b"OLD", "image/png", "A supplied screenshot")
    for index, request in enumerate(("Describe this image.", "What does its button say?")):
        await session._delegate(f"image-{index}", request)
        items = reasoning.requests[index]["input"]
        assert items[-1]["content"][0]["text"] == request
        assert "data:image/png;base64,T0xE" in json.dumps(items)
        assert "Earlier screen snapshot" in json.dumps(items)
    assert gateway.calls == []


@pytest.fixture
def image_store(monkeypatch):
    from jarvis.core import image_references

    now = [0.0]
    store = image_references.ImageReferences(clock=lambda: now[0], schedule_expiry=False)
    monkeypatch.setattr(image_references, "_STORE", store)
    return store, now


@pytest.mark.asyncio
async def test_supplied_appshot_carries_a_forwardable_reference_for_the_call(
    make_session, image_store,
):
    import re

    from jarvis.core.image_references import ImageReferenceError

    store, now = image_store
    session, reasoning, _, _, _, _ = make_session([
        completed_response("r1", [spoken_result("I can see the bug.")]),
        completed_response("r2", [spoken_result("Starting a coding session.")]),
    ])
    await session.attach_appshot(b"\x89PNG-shot", "image/png", "A supplied screenshot")
    scope = "live:" + session.session_id
    for index, request in enumerate(("What is wrong here?", "Start a session to fix it.")):
        await session._delegate(f"image-{index}", request)
        sent = json.dumps(reasoning.requests[index]["input"])
        ref = re.search(r"img_[0-9a-f]{32}", sent)[0]
        assert "image_refs" in sent
        # Talking past the capture budget must not orphan a picture still in context.
        now[0] += 600
        assert store.resolve(scope, [ref])[0].data == b"\x89PNG-shot"
    assert [row["source"] for row in store.available(scope)] == ["appshot"]
    with pytest.raises(ImageReferenceError):
        store.resolve("live:another-call", [ref])
    await session._tools.close()
    with pytest.raises(ImageReferenceError):
        store.resolve(scope, [ref])


@pytest.mark.asyncio
async def test_unforwardable_appshot_is_reported_instead_of_given_an_invented_reference(
    make_session, image_store,
):
    session, _, _, _, _, _ = make_session([])
    await session.attach_appshot(b"", "image/png", "An empty capture")
    note = session._pending_images[0]["text"]
    assert "Visual handoff unavailable" in note and "img_" not in note
    assert image_store[0].available("live:" + session.session_id) == []


@pytest.mark.asyncio
async def test_tool_captured_appshot_keeps_its_reference_beside_the_pixels(make_session):
    capture = {
        "id": "fresh-shot", "type": "function_call", "call_id": "capture-1",
        "name": "take_appshot", "arguments": '{"scope":"window"}',
    }
    handoff = "Visual reference IDs (same order as the attached images): img_fresh."
    session, _, _, _, _, _ = make_session([
        completed_response("r1", [capture]),
        completed_response("r2", [spoken_result("Captured.")]),
    ], gateway=AppshotSubscriptionGateway(handoff=handoff))
    await session._delegate("fresh-request", "Take a new appshot.")
    assert session._image_context == [
        {"type": "input_text", "text": handoff},
        {"type": "input_image", "image_url": "data:image/png;base64,TkVX"},
    ]


@pytest.mark.asyncio
async def test_completion_report_uses_subscription_reasoning_without_new_tool_authority(
    make_session,
):
    session, reasoning, _, _, _, _ = make_session(
        [
            completed_response("r1", [spoken_result("Your task completed.")]),
        ]
    )
    original_user = session._tools.user_text
    accepted = await session.deliver_announcement(
        "Task complete",
        report="The report was saved successfully.",
        language="en",
    )
    assert accepted
    await wait_for_jobs(session)
    assert reasoning.requests[0]["tools"] == []
    assert (
        "Application event, not the user speaking"
        in reasoning.requests[0]["input"][-1]["content"][0]["text"]
    )
    assert session._tools.user_text == original_user
    assert session._connection.sent[-1]["delegation_id"] is None
    assert not any(event["type"].startswith("response.") for event in session._connection.sent)
    session._thinking = True
    assert not await session.deliver_announcement("Another task", report="Some result")


def test_cached_paid_brain_cannot_escape_operation_billing_pin():
    from jarvis.brain.usage_meter import MeteredBrain
    from jarvis.core.model_selection import ModelSelection, use_operation_model
    from jarvis.core.protocols import BrainRequest

    class PaidTrap:
        def complete(self, request):
            raise AssertionError("A paid provider was called")

    cached = MeteredBrain(PaidTrap(), "openai")
    with use_operation_model(ModelSelection("subscription", "model", brain_override=object())):
        with pytest.raises(RuntimeError, match="billing account"):
            cached.complete(BrainRequest(messages=()))


def test_skill_author_cannot_continue_into_paid_fallbacks():
    from jarvis.core.model_selection import ModelSelection, use_operation_model
    from jarvis.skills.creator_service import SkillCreatorService

    class SubscriptionFailure:
        def complete(self, request):
            raise RuntimeError("Subscription unavailable")

    class PaidTrap:
        active_provider = "openai"

        def _get_or_create(self, *args):
            raise AssertionError("Paid fallback must not be resolved")

    service = SkillCreatorService.__new__(SkillCreatorService)
    service._brain = PaidTrap()
    service._seat_only = False
    service._config = object()
    selected = SubscriptionFailure()
    with use_operation_model(ModelSelection("subscription", "model", brain_override=selected)):
        candidates = service._candidate_brains()
        assert next(candidates)[0] is selected
        with pytest.raises(RuntimeError, match="Subscription unavailable"):
            selected.complete(None)
        assert list(candidates) == []


@pytest.mark.asyncio
async def test_typed_turns_retain_both_sides_and_initial_context(make_session):
    session, reasoning, _, _, _, _ = make_session(
        [
            completed_response("r1", [spoken_result("I will remember project Alpha.")]),
            completed_response("r2", [spoken_result("Project Alpha uses the original context.")]),
        ]
    )
    session._initial_seed = [{"role": "user", "delta": "We previously chose the blue design."}]
    await session.handle_control({"type": "text_input", "text": "The project is Alpha."})
    await wait_for_jobs(session)
    await session.handle_control({"type": "text_input", "text": "Explain that project."})
    await wait_for_jobs(session)
    second_input = json.dumps(reasoning.requests[1]["input"])
    assert "We previously chose the blue design." in second_input
    assert "The project is Alpha." in second_input
    assert "I will remember project Alpha." in second_input
    assert "Explain that project." in second_input
    saved = session._ledger.transcript(session.session_id)
    assert [row["delta"] for row in saved] == ["The project is Alpha.", "Explain that project."]


@pytest.mark.asyncio
async def test_application_report_cannot_consume_a_pending_user_appshot(make_session):
    session, reasoning, _, _, _, _ = make_session(
        [
            completed_response("r1", [spoken_result("The previous task completed.")]),
            completed_response("r2", [spoken_result("The image is visible.")]),
        ]
    )
    await session.attach_appshot(b"image", "image/png", "User screenshot")
    pending = list(session._pending_images)
    assert await session.deliver_announcement("Finished", report="A prior task completed.")
    await wait_for_jobs(session)
    assert session._pending_images == pending
    assert "input_image" not in json.dumps(reasoning.requests[0]["input"])
    await session.handle_control({"type": "text_input", "text": "Describe my screenshot."})
    await wait_for_jobs(session)
    assert not session._pending_images
    assert "input_image" in json.dumps(reasoning.requests[1]["input"])
    assert session._image_context == pending


@pytest.mark.asyncio
async def test_final_asr_correction_does_not_suppress_verified_result(make_session):
    session, reasoning, _, _, _, _ = make_session(
        [
            completed_response("r1", [spoken_result("The connectivity check passed.")]),
        ]
    )
    await session._event(
        {
            "type": "session.input_transcript.delta",
            "segment_id": "same-utterance",
            "transcript": "Check connection",
            "snapshot": True,
            "start_ms": 0,
            "end_ms": 10,
        }
    )
    reasoning.block = asyncio.Event()
    await session._event(
        {
            "type": "session.delegation.created",
            "delegation": {"id": "d1"},
            "prompt": "Check connection",
        }
    )
    await reasoning.started.wait()
    accepted_revision = session._tools.revision
    await session._event(
        {
            "type": "session.input_transcript.done",
            "segment_id": "same-utterance",
            "transcript": "Check the connection.",
            "snapshot": True,
            "is_final": True,
            "start_ms": 0,
            "end_ms": 20,
        }
    )
    assert session._tools.revision == accepted_revision
    reasoning.block.set()
    await wait_for_jobs(session)
    assert session._connection.sent[-1]["content"] == "The connectivity check passed."


@pytest.mark.asyncio
async def test_completed_output_supplies_final_message_after_streamed_reasoning(make_session):
    reasoning_item = {"id": "thinking-1", "type": "reasoning", "summary": []}
    session, _, _, _, _, _ = make_session(
        [
            [
                {"type": "response.created", "response": {"id": "r1"}},
                {"type": "response.output_item.done", "item": reasoning_item},
                {
                    "type": "response.completed",
                    "response": {
                        "id": "r1",
                        "output": [reasoning_item, spoken_result("The final answer.")],
                    },
                },
            ]
        ]
    )
    await session._delegate("d1", "Answer now")
    assert session._connection.sent[-1]["content"] == "The final answer."


@pytest.mark.asyncio
async def test_late_caption_cannot_reenable_cancelled_work(make_session):
    session, _, _, _, _, _ = make_session([])
    await session.handle_control({"type": "cancel_work"})
    await session._event(
        {
            "type": "session.input_transcript.done",
            "segment_id": "old-utterance",
            "transcript": "The cancelled request",
            "snapshot": True,
            "is_final": True,
            "start_ms": 0,
            "end_ms": 20,
        }
    )
    assert session._tools.cancel_token.is_cancelled()
    assert not session._tools.accepting


def test_explicit_api_provider_uses_retained_api_settings_after_subscription(monkeypatch):
    from jarvis.realtime import factory

    provider = SimpleNamespace(name="openai-live", continuous_conversation=True)
    monkeypatch.setattr(factory, "_provider_candidates", lambda *_args, **_kwargs: [provider])
    cfg = SimpleNamespace(
        voice=SimpleNamespace(mode="realtime"),
        brain=SimpleNamespace(
            reply_language="en", realtime=SimpleNamespace(provider="openai-live")
        ),
        live=LiveConfig(
            configured=True,
            auth_mode="chatgpt_subscription",
            backend_model="retained-api-model",
            subscription_backend_model="plan-model",
        ),
    )
    session = factory.build_realtime_session(
        cfg=cfg,
        bus=None,
        session_id="api-selection",
        send_binary=None,
        send_json=None,
    )
    wire = session._config.live.session_config(language="en", tools=[])
    assert wire["model"] == "gpt-live-1"
    assert wire["delegation"]["responses"]["model"] == "retained-api-model"
    assert cfg.live.auth_mode == "chatgpt_subscription"


@pytest.mark.parametrize(
    "provider,mode",
    [
        ("openai-live", "api_key"),
        ("openai-live-subscription", "chatgpt_subscription"),
    ],
)
def test_live_setup_reports_the_explicit_runtime_billing_path(provider, mode):
    from jarvis.ui.web.live_routes import _profile_for_runtime

    saved_mode = "chatgpt_subscription" if mode == "api_key" else "api_key"
    cfg = SimpleNamespace(
        brain=SimpleNamespace(realtime=SimpleNamespace(provider=provider)),
        live=LiveConfig(auth_mode=saved_mode),
    )
    assert _profile_for_runtime(cfg).auth_mode == mode
    assert cfg.live.auth_mode == saved_mode


async def test_subscription_report_waits_for_native_caption_completion_and_blocks_overlap(
    make_session,
):
    session, _, _, _, _, _ = make_session(
        [completed_response("r1", [spoken_result("The report is ready.")])]
    )
    assert await session.deliver_announcement("Ready", report="A verified finding.")
    assert session.report_pending and not session.ready_for_report
    await wait_for_jobs(session)
    assert session.take_report_outcome() == ""
    assert not await session.deliver_announcement("Another", report="Another finding.")
    await session._event(
        {
            "type": "session.output_transcript.delta",
            "segment_id": "report-output",
            "transcript": "The report",
            "snapshot": True,
            "start_ms": 10,
            "end_ms": 20,
        }
    )
    assert session.report_pending and session._report_state == "started"
    await session._event(
        {
            "type": "session.output_transcript.done",
            "segment_id": "report-output",
            "transcript": "The report is ready.",
            "snapshot": True,
            "is_final": True,
            "start_ms": 10,
            "end_ms": 30,
        }
    )
    assert session.take_report_outcome() == "completed"
    assert session.take_report_outcome() == ""


async def test_filler_before_report_submission_cannot_confirm_delivery(make_session):
    session, reasoning, _, _, _, _ = make_session([completed_response("r1", [spoken_result()])])
    reasoning.block = asyncio.Event()
    assert await session.deliver_announcement("Ready", report="A verified finding.")
    await reasoning.started.wait()
    await session._event(
        {
            "type": "session.output_transcript.done",
            "segment_id": "filler",
            "transcript": "Let me check.",
            "snapshot": True,
            "is_final": True,
            "start_ms": 1,
            "end_ms": 2,
        }
    )
    assert session._report_state == "sent"
    assert session.take_report_outcome() == ""
    reasoning.block.set()
    await wait_for_jobs(session)


async def test_failed_subscription_report_remains_owed_and_signals_pause(make_session, monkeypatch):
    import jarvis.core.runtime_refs as base

    paused = []
    monkeypatch.setattr(
        base,
        "get_speech_pipeline",
        lambda: SimpleNamespace(
            live_call_paused=lambda session: paused.append(session),
        ),
    )
    session, _, _, _, _, _ = make_session([[{"type": "response.failed"}]])
    assert await session.deliver_announcement("Ready", report="A verified finding.")
    await wait_for_jobs(session)
    assert session.take_report_outcome() == "failed"
    assert session in paused
    assert not session._connection.sent


async def test_unvoiced_report_times_out_only_after_result_submission(make_session):
    session, _, _, _, _, _ = make_session([completed_response("r1", [spoken_result()])])
    assert await session.deliver_announcement("Ready", report="A verified finding.")
    await wait_for_jobs(session)
    assert len(session._connection.sent) == 1
    assert session._report_timeout is not None
    session._cancel_report_timeout()
    session._report_start_timed_out()
    assert session.take_report_outcome() == "failed"


async def test_report_summary_outlasts_speech_budget_and_still_gets_voiced(
    make_session,
    monkeypatch,
):
    import jarvis.live.subscription as base

    monkeypatch.setattr(base, "REPORT_START_TIMEOUT_S", 0.01)
    session, reasoning, _, _, _, _ = make_session(
        [completed_response("r1", [spoken_result("The late summary is ready.")])]
    )
    reasoning.block = asyncio.Event()
    assert await session.deliver_announcement("Ready", report="A verified finding.")
    await reasoning.started.wait()
    assert session._report_timeout is None
    await asyncio.sleep(0.03)
    assert session.report_pending and session.take_report_outcome() == ""
    # Give the native caption an independent, generous startup budget.
    monkeypatch.setattr(base, "REPORT_START_TIMEOUT_S", 20.0)
    reasoning.block.set()
    await wait_for_jobs(session)
    assert session._report_timeout is not None
    assert session._connection.sent[-1]["content"] == "The late summary is ready."
    await session._event(
        {
            "type": "session.output_transcript.done",
            "segment_id": "late-summary",
            "transcript": "The late summary is ready.",
            "snapshot": True,
            "is_final": True,
            "start_ms": 10,
            "end_ms": 30,
        }
    )
    assert session._report_timeout is None
    session._report_start_timed_out()  # A stale callback cannot undo actual delivery.
    assert session.take_report_outcome() == "completed"


async def test_report_cancelled_before_inference_starts_does_not_stay_pending(make_session):
    session, reasoning, _, _, _, _ = make_session([])
    assert await session.deliver_announcement("Ready", report="A verified finding.")
    task = session._subscription_report_task
    assert task is not None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert not reasoning.requests
    assert session._report_timeout is None
    assert session.take_report_outcome() == "failed"
    assert session.ready_for_report


async def test_empty_report_summary_does_not_stay_pending(make_session):
    session, _, _, _, _, _ = make_session([completed_response("r1", [])])
    assert await session.deliver_announcement("Ready", report="A verified finding.")
    await wait_for_jobs(session)
    assert not session._connection.sent
    assert session._report_timeout is None
    assert session.take_report_outcome() == "failed"


async def test_fresh_smalltalk_cannot_acknowledge_an_unspoken_report(make_session):
    session, _, _, _, _, _ = make_session([completed_response("r1", [spoken_result()])])
    assert await session.deliver_announcement("Ready", report="A verified finding.")
    await wait_for_jobs(session)
    revision = session._tools.revision
    await session._event(
        {
            "type": "session.input_transcript.delta",
            "segment_id": "greeting",
            "transcript": "Hello",
            "snapshot": True,
            "start_ms": 10,
            "end_ms": 20,
        }
    )
    await session._event(
        {
            "type": "session.output_transcript.done",
            "segment_id": "greeting-response",
            "transcript": "Hello there.",
            "snapshot": True,
            "is_final": True,
            "start_ms": 21,
            "end_ms": 30,
        }
    )
    assert session.take_report_outcome() == "failed"
    assert session._tools.revision == revision


async def test_same_user_segment_correction_does_not_invalidate_report(make_session):
    session, _, _, _, _, _ = make_session([completed_response("r1", [spoken_result()])])
    await session._event(
        {
            "type": "session.input_transcript.delta",
            "segment_id": "prior-request",
            "transcript": "Read the report",
            "snapshot": True,
            "start_ms": 1,
            "end_ms": 2,
        }
    )
    assert await session.deliver_announcement("Ready", report="A verified finding.")
    await wait_for_jobs(session)
    revision = session._tools.revision
    await session._event(
        {
            "type": "session.input_transcript.done",
            "segment_id": "prior-request",
            "transcript": "Read the report.",
            "snapshot": True,
            "is_final": True,
            "start_ms": 1,
            "end_ms": 3,
        }
    )
    assert session.report_pending
    assert session._tools.revision == revision
    await session._event(
        {
            "type": "session.output_transcript.done",
            "segment_id": "report-answer",
            "transcript": "The state is verified.",
            "snapshot": True,
            "is_final": True,
            "start_ms": 10,
            "end_ms": 30,
        }
    )
    assert session.take_report_outcome() == "completed"


@pytest.mark.parametrize("code", ["authentication_required", "quota_exhausted", "rate_limited"])
async def test_terminal_subscription_reconnect_error_stops_shared_retry_loop(
    make_session,
    monkeypatch,
    code,
):
    import jarvis.live.recovery as recovery
    import jarvis.live.session as base
    from jarvis.plugins.realtime.openai_subscription_live import SubscriptionLiveError

    attempts, permits = [], []

    async def permit():
        permits.append(True)

    async def reattach(previous):
        attempts.append(previous)
        raise SubscriptionLiveError(code)

    monkeypatch.setattr(recovery, "connection_permit", permit)
    monkeypatch.setattr(base.random, "uniform", lambda *args: 0)
    session, _, _, _, _, _ = make_session([])
    session._provider.reattach_session = reattach
    assert not await session._wait_for_connection()
    assert len(attempts) == len(permits) == 1
    assert session._ended and session.failed
    assert session.failure_detail == code


async def test_subscription_transient_control_outage_reuses_call_and_shared_budget(
    make_session,
    monkeypatch,
):
    import jarvis.live.recovery as recovery
    import jarvis.live.session as base

    attempts, permits = [], []
    restored = SubscriptionConnection()

    async def permit():
        permits.append(True)

    async def reattach(previous):
        attempts.append(previous)
        if len(attempts) < 3:
            raise OSError("offline")
        return restored

    monkeypatch.setattr(recovery, "connection_permit", permit)
    monkeypatch.setattr(base.random, "uniform", lambda *args: 0)
    session, _, _, messages, _, _ = make_session([])
    old = session._connection
    session._provider.reattach_session = reattach
    assert await session._wait_for_connection()
    assert len(attempts) == len(permits) == 3
    assert all(previous is old for previous in attempts)
    assert session._connection is restored and old.closed
    assert messages[-1]["reuse_webrtc"] is True
    assert not session.failed


async def test_terminal_subscription_recovery_on_pump_task_still_closes_resources(
    make_session,
    monkeypatch,
):
    import jarvis.live.recovery as recovery
    import jarvis.live.session as base
    from jarvis.plugins.realtime.openai_subscription_live import SubscriptionLiveError

    async def permit():
        return None

    async def reattach(previous):
        raise SubscriptionLiveError("authentication_required")

    monkeypatch.setattr(recovery, "connection_permit", permit)
    monkeypatch.setattr(base.random, "uniform", lambda *args: 0)
    session, reasoning, _, _, _, _ = make_session([])
    session._provider.reattach_session = reattach
    session._pump_task = asyncio.create_task(session._wait_for_connection())
    assert await session._pump_task is False
    assert session._connection.closed and reasoning.closed


async def test_new_user_request_does_not_acknowledge_an_unspoken_report(make_session):
    session, _, _, _, _, _ = make_session(
        [
            completed_response("report", [spoken_result("Report result")]),
            completed_response("user", [spoken_result("New request result")]),
        ]
    )
    assert await session.deliver_announcement("Ready", report="A verified finding.")
    await wait_for_jobs(session)
    assert session.report_pending
    await session._event(
        {
            "type": "session.delegation.created",
            "delegation": {"id": "new-request"},
            "prompt": "Answer my new request",
        }
    )
    await wait_for_jobs(session)
    await session._event(
        {
            "type": "session.output_transcript.done",
            "segment_id": "new-request-answer",
            "transcript": "New request result",
            "snapshot": True,
            "is_final": True,
            "start_ms": 10,
            "end_ms": 30,
        }
    )
    assert session.take_report_outcome() == "failed"


async def test_filler_started_before_submission_cannot_acknowledge_report_late(make_session):
    session, reasoning, _, _, _, _ = make_session(
        [completed_response("r1", [spoken_result("Verified report result")])]
    )
    reasoning.block = asyncio.Event()
    assert await session.deliver_announcement("Ready", report="Verified report data")
    await reasoning.started.wait()
    await session._event(
        {
            "type": "session.output_transcript.delta",
            "segment_id": "old-filler",
            "transcript": "Let me",
            "snapshot": True,
            "start_ms": 1,
            "end_ms": 2,
        }
    )
    reasoning.block.set()
    await wait_for_jobs(session)
    assert session._subscription_report_submitted
    await session._event(
        {
            "type": "session.output_transcript.done",
            "segment_id": "old-filler",
            "transcript": "Let me check",
            "snapshot": True,
            "is_final": True,
            "start_ms": 1,
            "end_ms": 5,
        }
    )
    assert session.report_pending
    assert session.take_report_outcome() == ""
    await session._event(
        {
            "type": "session.output_transcript.done",
            "segment_id": "actual-report",
            "transcript": "Verified report result",
            "snapshot": True,
            "is_final": True,
            "start_ms": 6,
            "end_ms": 10,
        }
    )
    assert session.take_report_outcome() == "completed"


@pytest.mark.parametrize("terminal", [False, True])
async def test_subscription_failure_uses_browser_status_contract(make_session, terminal):
    session, _, _, messages, _, _ = make_session([])
    await session._failure("untrusted-provider-body", terminal=terminal)
    expected = "provider_error" if terminal else "provider_warning"
    failure = next(message for message in messages if message.get("type") == expected)
    assert failure["code"] == failure["reason"] == "subscription_unavailable"
    assert isinstance(failure["error"], str) and failure["error"]
    assert "untrusted-provider-body" not in json.dumps(failure)
    assert session._ended is terminal
    assert session.failed is terminal
    assert session._connection.closed is terminal


async def test_report_reasoning_deadline_returns_failed_receipt(make_session, monkeypatch):
    import jarvis.live.subscription as module

    monkeypatch.setattr(module, "REPORT_REASONING_TIMEOUT_S", 0.01)
    session, reasoning, _, messages, _, _ = make_session([])
    reasoning.block = asyncio.Event()
    assert await session.deliver_announcement("Ready", report="Verified report data")
    await asyncio.wait_for(wait_for_jobs(session), 1)
    assert session.take_report_outcome() == "failed"
    assert not session._connection.sent
    assert any(message.get("type") == "provider_warning" for message in messages)


async def test_started_report_without_final_caption_has_bounded_receipt(make_session):
    session, _, _, _, _, _ = make_session([completed_response("r1", [spoken_result()])])
    assert await session.deliver_announcement("Ready", report="Verified report data")
    await wait_for_jobs(session)
    await session._event(
        {
            "type": "session.output_transcript.delta",
            "segment_id": "report-answer",
            "transcript": "Verified",
            "snapshot": True,
            "start_ms": 1,
            "end_ms": 2,
        }
    )
    assert session._report_timeout is not None
    session._report_start_timed_out()
    assert session.take_report_outcome() == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("current", ["What's up?", "Continue with the agent I mentioned."])
async def test_archived_request_is_context_for_the_current_delegation(make_session, current):
    session, reasoning, gateway, _, _, _ = make_session(
        [completed_response("reply", [spoken_result("I am here.")])]
    )
    previous = "Prompt the Codex agent in the Computer Use workspace."
    session._initial_seed = [{"role": "user", "delta": previous}]
    session._tools.user_text = current
    await session._event({
        "type": "session.delegation.created",
        "delegation": {"id": "current-request"},
        "prompt": current,
    })
    await wait_for_jobs(session)
    items = reasoning.requests[0]["input"]
    assert items[0]["role"] == "assistant"
    assert previous in items[0]["content"][0]["text"]
    user_texts = [
        part["text"] for item in items if item.get("role") == "user"
        for part in item["content"] if part.get("type") == "input_text"
    ]
    assert user_texts[-1] == current
    assert previous not in user_texts
    assert gateway.calls == []


def test_subscription_and_api_share_explicit_voice_and_thinking_identity():
    identity = "Your name is Lyra. Respect the user's saved identity instructions."
    for mode in ("api_key", "chatgpt_subscription"):
        profile = LiveConfig(
            configured=True, auth_mode=mode, backend_model="api-model",
            subscription_backend_model="subscription-model",
        )
        session = profile.session_config(language="en", tools=[], identity=identity)
        backend = profile.backend_config(language="en", tools=[], identity=identity)
        assert session["instructions"].startswith(identity)
        assert backend["instructions"].startswith(identity)
        assert "Jarvis then asks its own hang-up confirmation" in backend["instructions"]
        assert session["delegation"]["type"] == (
            "client" if mode == "chatgpt_subscription" else "responses"
        )


async def test_subscription_reasoning_inherits_configured_identity(make_session, monkeypatch):
    import jarvis.live.subscription as module

    monkeypatch.setattr(module, "_identity", lambda config: "Your name is Lyra.")
    session, reasoning, _, _, _, _ = make_session(
        [completed_response("r1", [spoken_result()])]
    )
    await session._delegate("d1", "Inspect state")
    assert reasoning.requests[0]["instructions"].startswith("Your name is Lyra.")


async def test_subscription_keeps_jarvis_hangup_confirmation_and_model_pin(make_session):
    session, _, _, messages, _, _ = make_session([])
    session._tools.ask_hangup = session._ask_voice_hangup
    session._tools.user_text = "Hang up"
    session._tools.revision = 1
    first = await session._tools.execute("hangup-request", "end_call", {}, 1)
    assert first["confirmation_required"] and not session._tools.end_requested
    assert any(message.get("type") == "error_spoken" for message in messages)
    assert session._tools.model_selection.provider == "openai-chatgpt-subscription"
    session._tools.user_text = "Yes"
    session._tools.revision = 2
    confirmed = await session._tools.execute("hangup-confirm", "end_call", {}, 2)
    assert confirmed["success"] and session._tools.end_requested
