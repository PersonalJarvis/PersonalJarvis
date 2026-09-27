"""End-to-end communication contracts using SQLite and the real scheduler.

No provider, native API, microphone or display is needed on any supported OS.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.plugins.tool.delegate_to_agent import DelegateToAgentTool
from jarvis.plugins.tool.message_agent import LeadMessageAgentTool
from jarvis.society.agent_tools import MessageAgentTool
from jarvis.society.chat_binding import frame_assignment, frame_incoming, make_deliver_hook
from jarvis.society.communication import reply_policy
from jarvis.society.delivery import IncomingMessage, incoming_context
from jarvis.society.events import MsgType, SocietyEnvelope
from jarvis.society.runtime import SocietyRuntime


class Chat:
    receive_message = AgentChatService.receive_message
    message_status = AgentChatService.message_status

    def __init__(self, path):
        self.store = AgentChatStore(path)
        self.queues = {}
        self.sent = []
        self.notices = []
        self.contexts = []

    async def _emit(self, session_id, event):
        self.store.append_event(session_id, event)

    def is_running(self, session_id):
        return False

    def subscribe(self, session_id):
        queue = asyncio.Queue()
        self.queues[session_id] = queue
        return queue

    def unsubscribe(self, session_id, queue):
        self.queues.pop(session_id, None)

    async def send(self, session_id, text, **kwargs):
        self.sent.append((session_id, text))
        self.contexts.append(incoming_context.get())
        return f"turn-{len(self.sent)}"

    async def post_notice(self, session_id, payload):
        self.notices.append(payload)


@pytest.fixture
async def world(tmp_path):
    chat = Chat(tmp_path / "chat.db")
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path)))
    published = []
    runtime = SocietyRuntime(
        tmp_path,
        chat_service=lambda: chat,
        cfg=lambda: cfg,
        event_publish=lambda event: (
            published.append(event) if type(event).__name__ == "AnnouncementRequested" else None
        ),
    )
    await runtime.ensure_started()
    await runtime.roster.create(name="Scout", provider="openai")
    await runtime.roster.create(name="Archivist", provider="gemini")
    chat.store.create_session(
        provider="openai",
        model="",
        effort="",
        cwd=str(tmp_path),
        surface="jarvis",
    )
    runtime.set_deliver(make_deliver_hook(lambda: chat, lambda: cfg))
    try:
        yield runtime, chat, published
    finally:
        await runtime.close()
        chat.store.close()


def ctx():
    return SimpleNamespace(trace_id=uuid4(), config={"output_language": "en"})


async def send(rt, sender="jarvis", target="scout", **args):
    tool = MessageAgentTool(rt, sender)
    result = await tool.execute({"target": target, "text": "Known test context.", **args}, ctx())
    assert result.success, result.error
    return await rt.store.get_event(result.output["message_id"])


def completion(text="Measured result.", status="completed", direct_user=False):
    events = [
        {"kind": "assistant_text", "payload": {"text": text}},
        {"kind": "turn_finished", "payload": {"status": status}},
    ]
    return SimpleNamespace(
        turn=SimpleNamespace(direct_user=direct_user, turn_id="t"),
        events_json=json.dumps(events),
    ), events


async def finish_message(rt, chat, request, **kwargs):
    origin = IncomingMessage(
        message_id=request.event_id,
        sender_id=request.from_agent,
        sender_name=request.from_agent,
        sender_kind="jarvis",
        text=request.text,
        prompt=frame_incoming(request, request.from_agent),
        trace_id=request.trace_id,
    )
    token = incoming_context.set(origin)
    try:
        done, events = completion(**kwargs)
        await rt._complete_message_reply(
            chat.store.get_session(f"society:{request.to_agent}"),
            done,
            events,
        )
    finally:
        incoming_context.reset(token)


async def test_question_returns_once_and_answer_ends_exchange(world):
    rt, chat, published = world
    request = await send(rt, kind="query")
    assert reply_policy(request) == "always"
    assert "Known test context." in chat.sent[0][1]
    assert "kind 'answer'" in chat.sent[0][1]
    await finish_message(rt, chat, request, text="Navigation and reading succeeded.")
    await finish_message(rt, chat, request, text="Duplicate callback.")
    answers = [
        e for e in await rt.store.events_for_trace(request.trace_id) if e.msg_type is MsgType.ANSWER
    ]
    assert len(answers) == 1
    assert answers[0].parent_event_id == request.event_id
    assert answers[0].text == "Navigation and reading succeeded."
    assert reply_policy(answers[0]) == "none"
    assert "No reply is requested" in frame_incoming(answers[0], "Scout")
    assert len(chat.notices) == len(published) == 1
    refused = await MessageAgentTool(rt, "jarvis").execute(
        {
            "target": "scout",
            "text": "Thanks.",
            "kind": "answer",
            "in_reply_to": answers[0].event_id,
        },
        ctx(),
    )
    assert not refused.success


async def test_delayed_reply_uses_durable_parent_and_no_duplicate_fallback(world):
    rt, chat, _ = world
    request = await send(rt, kind="query")
    assert incoming_context.get() is None
    result = await MessageAgentTool(rt, "scout").execute(
        {
            "target": "jarvis",
            "text": "Here are the findings.",
            "kind": "answer",
            "in_reply_to": request.event_id,
        },
        ctx(),
    )
    assert result.success
    assert result.output["trace_id"] == request.trace_id
    await finish_message(rt, chat, request)
    answers = [
        e for e in await rt.store.events_for_trace(request.trace_id) if e.msg_type is MsgType.ANSWER
    ]
    assert len(answers) == 1
    wrong = await MessageAgentTool(rt, "archivist").execute(
        {
            "target": "jarvis",
            "text": "Forged reply.",
            "kind": "answer",
            "in_reply_to": request.event_id,
        },
        ctx(),
    )
    assert not wrong.success


@pytest.mark.parametrize(
    "policy,status,expected",
    [
        ("none", "completed", 0),
        ("none", "error", 0),
        ("on_error", "completed", 0),
        ("on_error", "error", 1),
        ("always", "completed", 1),
        ("always", "error", 1),
    ],
)
async def test_message_reporting_matrix(world, policy, status, expected):
    rt, chat, published = world
    request = await send(rt, reply_policy=policy)
    await finish_message(rt, chat, request, status=status)
    assert len(chat.notices) == len(published) == expected
    if expected and status == "error":
        replies = await rt.store.events_for_trace(request.trace_id)
        assert replies[-1].payload["reply_status"] == "blocked"


async def test_direct_user_turn_and_unrelated_parent_never_auto_forward(world):
    rt, chat, published = world
    request = await send(rt, kind="query")
    await finish_message(rt, chat, request, direct_user=True)
    await rt.say(
        from_agent="scout",
        to_agent="jarvis",
        text="Unrelated private conversation.",
        msg_type=MsgType.ANSWER,
        trace_id=request.trace_id,
        parent_event_id="not-the-request",
    )
    assert not published and not chat.notices


@pytest.mark.parametrize(
    "policy,status,expected",
    [
        ("none", "done", 0),
        ("none", "blocked", 0),
        ("on_error", "done", 0),
        ("on_error", "blocked", 1),
        ("always", "done", 1),
        ("always", "blocked", 1),
    ],
)
async def test_assignment_reporting_preserves_result_and_releases_slot(
    world, policy, status, expected
):
    rt, chat, published = world
    result = await DelegateToAgentTool(runtime_resolver=lambda: rt).execute(
        {
            "agent": "scout",
            "task": "Verify browser navigation.",
            "context": "Use the agent-owned browser.",
            "completion_criteria": "Record the actual URL and observed navigation result.",
            "reply_policy": policy,
        },
        ctx(),
    )
    assert result.success
    request = await rt.store.get_event(result.output["assignment_id"])
    assert request.payload["reply_policy"] == policy
    assert "agent-owned browser" in request.text
    assert "observed navigation" in frame_assignment(request)
    assert chat.contexts[-1].message_id == request.event_id
    if policy != "always":
        assert "I will let you know" not in result.output["acknowledgement"]
    if status == "blocked":
        reported = await MessageAgentTool(rt, "scout").execute(
            {
                "target": "jarvis",
                "text": "Browser access is unavailable.",
                "kind": "answer",
                "reply_status": "blocked",
                "in_reply_to": request.event_id,
            },
            ctx(),
        )
        assert reported.success
        assert not published  # The watcher owns the single completion announcement.
        assert not any(
            chat.store.incoming_message(session.session_id, reported.output["message_id"])
            for session in chat.store.list_sessions(surface="jarvis")
        )
    queue = chat.queues["society:scout"]
    await queue.put({"kind": "assistant_text", "payload": {"text": "Recorded outcome."}})
    await queue.put({"kind": "turn_finished", "payload": {"status": "completed"}})
    watchers = list(rt._watchers)
    await asyncio.wait_for(asyncio.gather(*watchers), timeout=5)
    results = [
        e for e in await rt.store.events_for_trace(request.trace_id) if e.msg_type is MsgType.RESULT
    ]
    assert len(results) == 1 and results[0].payload["status"] == status
    assert rt.scheduler.active_runs("scout") == 0
    assert len(chat.notices) == len(published) == expected


@pytest.mark.parametrize(
    "args",
    [
        {"kind": "query", "reply_policy": "none"},
        {"reply_policy": "unknown"},
        {"reply_policy": []},
        {"reply_status": "imagined"},
        {"text": "x" * 8001},
        {"kind": "answer"},
    ],
)
async def test_invalid_contract_does_not_queue_a_message(world, args):
    rt, chat, _ = world
    result = await LeadMessageAgentTool(runtime_resolver=lambda: rt).execute(
        {"target": "scout", "text": "Question.", **args},
        ctx(),
    )
    assert not result.success
    assert not chat.sent


def test_legacy_defaults_and_serialization():
    assignment = SocietyEnvelope(
        msg_type=MsgType.ASSIGN,
        from_agent="jarvis",
        to_agent="scout",
        trace_id="old",
    )
    assert reply_policy(assignment) == "always"
    query = assignment.model_copy(update={"msg_type": MsgType.QUERY})
    assert reply_policy(query) == "always"
    for policy in ("none", "on_error", "always"):
        original = assignment.model_copy(update={"payload": {"reply_policy": policy}})
        assert (
            reply_policy(SocietyEnvelope.model_validate_json(original.model_dump_json())) == policy
        )


async def test_real_chat_completion_hook_returns_final_answer(tmp_path, monkeypatch):
    from jarvis.agent_chat import service as service_module
    from jarvis.agent_chat.events import make_event

    async def receiver(handle, prompt, **kwargs):
        await handle.emit(
            make_event(
                "assistant_text",
                {
                    "turn_id": handle.turn_id,
                    "text": "The measured answer is 42.",
                },
            )
        )
        await handle.emit(
            make_event(
                "turn_finished",
                {
                    "turn_id": handle.turn_id,
                    "status": "completed",
                },
            )
        )

    async def no_review(runtime):
        # Learning is independent of returning the requested answer.
        return None

    monkeypatch.setattr(service_module, "run_brain_turn", receiver)
    monkeypatch.setattr(SocietyRuntime, "recover_reviews", no_review)
    chat = AgentChatService(AgentChatStore(tmp_path / "chat.db"))
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path)))
    rt = SocietyRuntime(tmp_path, chat_service=lambda: chat, cfg=lambda: cfg)
    await rt.ensure_started()
    await rt.roster.create(name="Scout", provider="openai")
    chat.store.create_session(
        provider="openai",
        model="",
        effort="",
        cwd=str(tmp_path),
        surface="jarvis",
    )
    rt.set_deliver(make_deliver_hook(lambda: chat, lambda: cfg))
    try:
        request = await send(rt, kind="query")
        for _ in range(100):
            rows = await rt.store.events_for_trace(request.trace_id)
            answers = [row for row in rows if row.msg_type is MsgType.ANSWER]
            if answers:
                break
            await asyncio.sleep(0.01)
        assert len(answers) == 1
        assert answers[0].text == "The measured answer is 42."
        assert answers[0].parent_event_id == request.event_id
    finally:
        await chat.cancel_all()
        await rt.close()
        chat.store.close()
