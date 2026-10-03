"""Jarvis and the agents talk in their own conversation chats.

Live bug 2026-10-02: when Jarvis (or a teammate) messaged an agent, the message
and the agent's whole work turn landed in the person's own chat with that
agent. Every sender now gets its own chat on the receiving side,
``society:<agent>:with:<sender>``, with the agent's full identity: the same
seat, tools, memory and briefing as the person's chat.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI

from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.society.chat_binding import make_deliver_hook
from jarvis.society.conversation import ConversationArchive
from jarvis.society.events import MsgType
from jarvis.society.roster import (
    canonical_session_id,
    conversation_session_id,
    pair_session_id,
)
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.surface import agent_id_of, counterpart_of


class TurnService:
    """The agent-chat surface the society drives, without a model behind it."""

    receive_message = AgentChatService.receive_message
    message_status = AgentChatService.message_status

    def __init__(self, store: AgentChatStore) -> None:
        self.store = store
        self.sent: list[tuple[str, str]] = []
        self.busy: set[str] = set()
        self.approvals: dict[str, list[str]] = {}
        self.queues: dict[str, list[asyncio.Queue]] = {}

    async def _emit(self, session_id, event):
        self.store.append_event(session_id, event)

    def is_running(self, session_id: str) -> bool:
        return session_id in self.busy

    def running_session_ids(self) -> list[str]:
        return sorted(self.busy)

    def pending_approvals(self, session_id: str) -> list[str]:
        return list(self.approvals.get(session_id, []))

    def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.queues.setdefault(session_id, []).append(q)
        return q

    def unsubscribe(self, session_id: str, q) -> None:
        self.queues.get(session_id, []).remove(q)

    async def send(self, session_id: str, text: str, attachments=None, **_: object) -> str:
        self.sent.append((session_id, text))
        return "turn-1"

    async def finish(self, session_id: str, text: str) -> None:
        for q in list(self.queues.get(session_id, [])):
            q.put_nowait({"kind": "assistant_text", "payload": {"turn_id": "turn-1", "text": text}})
            q.put_nowait(
                {"kind": "turn_finished", "payload": {"turn_id": "turn-1", "status": "ok"}}
            )


def _runtime(tmp_path: Path, svc: TurnService) -> SocietyRuntime:
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    return SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: svc, cfg=lambda: cfg
    )


def test_session_ids_name_the_owner_and_the_counterpart():
    assert pair_session_id("scout", "jarvis") == "society:scout:with:jarvis"
    assert conversation_session_id("scout", "jarvis") == "society:scout:with:jarvis"
    assert conversation_session_id("scout", "nova") == "society:scout:with:nova"
    # The person always speaks in the agent's own chat.
    assert conversation_session_id("scout", "user") == canonical_session_id("scout")
    assert conversation_session_id("scout", "") == "society:scout"
    # Every chat of an agent resolves to that agent's identity (tools, memory, briefing).
    assert agent_id_of("society:scout:with:jarvis") == "scout"
    assert agent_id_of("society:scout:with:mail-bot") == "scout"
    assert agent_id_of("society:scout:routine:t1:r1") == "scout"
    assert agent_id_of("society:scout") == "scout"
    assert counterpart_of("society:scout:with:jarvis") == "jarvis"
    assert counterpart_of("society:scout") is None


async def test_jarvis_assignment_runs_in_its_own_chat_not_the_persons(tmp_path: Path):
    svc = TurnService(AgentChatStore(tmp_path / "agent_chat.db"))
    rt = _runtime(tmp_path, svc)
    await rt.ensure_started()
    try:
        await rt.roster.create(name="Scout", provider="openai", model="gpt-5.2")
        env = await rt.say(
            from_agent="jarvis", to_agent="scout", text="Find a VPS.", msg_type=MsgType.ASSIGN
        )
        assert [sid for sid, _ in svc.sent] == ["society:scout:with:jarvis"]
        assert svc.store.get_session("society:scout") is None
        pair = svc.store.get_session("society:scout:with:jarvis")
        assert pair is not None and pair.surface == "society"
        assert pair.provider == "openai" and pair.model == "gpt-5.2"
        assert pair.title.startswith("Scout · ")

        await svc.finish("society:scout:with:jarvis", "Hetzner wins.")
        await asyncio.sleep(0.05)
        result = (await rt.store.events_for_trace(env.trace_id))[-1]
        assert result.msg_type is MsgType.RESULT
        assert result.payload["output"] == ["chat:society:scout:with:jarvis"]
    finally:
        await rt.close()


async def test_the_persons_assignment_stays_in_the_agents_own_chat(tmp_path: Path):
    svc = TurnService(AgentChatStore(tmp_path / "agent_chat.db"))
    rt = _runtime(tmp_path, svc)
    await rt.ensure_started()
    try:
        await rt.roster.create(name="Scout", provider="openai")
        await rt.say(from_agent="user", to_agent="scout", text="x", msg_type=MsgType.ASSIGN)
        assert [sid for sid, _ in svc.sent] == ["society:scout"]
    finally:
        await rt.close()


async def test_messages_wait_while_the_agent_works_in_any_chat(tmp_path: Path):
    svc = TurnService(AgentChatStore(tmp_path / "agent_chat.db"))
    rt = _runtime(tmp_path, svc)
    await rt.ensure_started()
    try:
        await rt.roster.create(name="Scout", provider="openai")
        await rt.roster.create(name="Nova", provider="openai")
        rt.set_deliver(make_deliver_hook(lambda: svc, rt.config))
        # Scout works in its conversation with Jarvis: Nova's message waits,
        # so two message turns never share Scout's seat and workspace.
        svc.busy.add("society:scout:with:jarvis")
        await rt.say(from_agent="nova", to_agent="scout", text="second")
        await rt.scheduler.drain_deliveries()
        assert svc.sent == []
        svc.busy.clear()
        await rt.scheduler.drain_deliveries()
        assert [sid for sid, _ in svc.sent] == ["society:scout:with:nova"]
        assert svc.store.get_session("society:scout") is None
    finally:
        await rt.close()


async def test_the_person_can_step_into_a_conversation_and_apply_a_change(tmp_path: Path):
    from jarvis.core.chat_turn import ChatTurn, current_chat_turn
    from jarvis.society.agent_tools import ProposeChangeTool

    svc = TurnService(AgentChatStore(tmp_path / "agent_chat.db"))
    rt = _runtime(tmp_path, svc)
    await rt.ensure_started()
    try:
        await rt.roster.create(name="Scout", provider="openai")
        quote = "Always cite your sources"
        args = {"kind": "rule", "mode": "apply", "request_quote": quote, "payload": {"text": quote}}
        tool = ProposeChangeTool(rt, "scout", session_id="society:scout:with:jarvis")
        results = {}
        for direct in (True, False):
            token = current_chat_turn.set(
                ChatTurn("society:scout:with:jarvis", "turn-1", quote, direct, "trace")
            )
            try:
                results[direct] = await tool.execute(args, SimpleNamespace(config={}))
            finally:
                current_chat_turn.reset(token)
        # The person typed it in the conversation chat: it applies there too.
        assert results[True].success, results[True].error
        # A message from Jarvis is not the person's request.
        assert not results[False].success
    finally:
        await rt.close()


def test_recall_searches_every_chat_the_agent_owns(tmp_path: Path):
    archive = ConversationArchive(tmp_path / "conversations.db")
    try:
        archive.ingest(
            "society:scout",
            [{"seq": 1, "kind": "user_message", "payload": {"text": "the person likes Hetzner"}}],
        )
        archive.ingest(
            "society:scout:with:jarvis",
            [{"seq": 1, "kind": "assistant_text", "payload": {"text": "Hetzner CX22 chosen"}}],
        )
        archive.ingest(
            "society:scout-two",
            [{"seq": 1, "kind": "assistant_text", "payload": {"text": "Hetzner elsewhere"}}],
        )
        own = archive.search("society:scout", "Hetzner", include_owned=True)
        assert sorted(hit["source"] for hit in own) == [
            "chat:society:scout#seq=1",
            "chat:society:scout:with:jarvis#seq=1",
        ]
        only = archive.search("society:scout", "Hetzner")
        assert [hit["source"] for hit in only] == ["chat:society:scout#seq=1"]
    finally:
        archive.close()


def test_mcp_session_header_accepts_conversation_chats():
    from jarvis.ui.web.mcp_server_routes import session_ref

    def ref(value: str) -> str | None:
        return session_ref({"headers": [(b"x-jarvis-chat-session", value.encode())]})

    assert ref("society:scout:with:jarvis") == "society:scout:with:jarvis"
    assert ref("society:scout:with:mail-bot") == "society:scout:with:mail-bot"
    assert ref("society:scout:with:../x") is None


async def test_conversations_route_lists_an_agents_chats(tmp_path: Path):
    import httpx

    from jarvis.ui.web import society_routes

    svc = TurnService(AgentChatStore(tmp_path / "agent_chat.db"))
    rt = _runtime(tmp_path, svc)
    await rt.ensure_started()
    try:
        await rt.roster.create(name="Scout", provider="openai")
        await rt.roster.create(name="Nova", provider="openai")
        rt.set_deliver(make_deliver_hook(lambda: svc, rt.config))
        for sender, target in (("jarvis", "scout"), ("nova", "scout"), ("jarvis", "nova")):
            await rt.say(from_agent=sender, to_agent=target, text="hi")
        svc.approvals["society:scout:with:nova"] = ["approval-1"]
        app = FastAPI()
        app.include_router(society_routes.router)
        app.state.society = rt
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            scout = (await client.get("/api/society/agents/scout/conversations")).json()
            lead = (await client.get("/api/society/agents/jarvis/conversations")).json()
    finally:
        await rt.close()
    assert sorted(row["session_id"] for row in scout["conversations"]) == [
        "society:scout:with:jarvis",
        "society:scout:with:nova",
    ]
    assert {row["counterpart_id"] for row in scout["conversations"]} == {"jarvis", "nova"}
    # An approval card waiting in a conversation shows on its chip.
    waiting = {row["counterpart_id"]: row["waiting"] for row in scout["conversations"]}
    assert waiting == {"jarvis": False, "nova": True}
    # Jarvis' card lists every agent's chat with Jarvis, named by that agent.
    assert sorted(row["session_id"] for row in lead["conversations"]) == [
        "society:nova:with:jarvis",
        "society:scout:with:jarvis",
    ]
    assert {row["counterpart_name"] for row in lead["conversations"]} == {"Scout", "Nova"}


async def test_a_message_records_the_chat_it_was_written_in(tmp_path: Path):
    from jarvis.core.chat_turn import ChatTurn, current_chat_turn
    from jarvis.society.agent_tools import MessageAgentTool

    svc = TurnService(AgentChatStore(tmp_path / "agent_chat.db"))
    rt = _runtime(tmp_path, svc)
    await rt.ensure_started()
    try:
        await rt.roster.create(name="Scout", provider="openai")
        await rt.roster.create(name="Nova", provider="openai")
        token = current_chat_turn.set(
            ChatTurn("society:scout:with:jarvis", "turn-1", "", False, "trace")
        )
        try:
            result = await MessageAgentTool(rt, "scout").execute(
                {"target": "nova", "text": "FYI: the VPS is chosen.", "kind": "say"},
                SimpleNamespace(config={}),
            )
        finally:
            current_chat_turn.reset(token)
        assert result.success, result.error
        sent = await rt.store.get_event(result.output["message_id"])
        # The person's chat with Scout uses this to leave the reply where it was written.
        assert sent.payload["from_session"] == "society:scout:with:jarvis"
    finally:
        await rt.close()
