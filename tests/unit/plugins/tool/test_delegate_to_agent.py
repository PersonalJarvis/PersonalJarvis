"""The society voice tools: ack at once, veto read-back, status without a model."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.plugins.tool.delegate_to_agent import DelegateToAgentTool, SocietyStatusTool
from jarvis.society.events import MsgType
from jarvis.society.runtime import SocietyRuntime


class FakeChat:
    """Enough of AgentChatService for a dispatch: the turn never ends here."""

    def __init__(self, db: Path) -> None:
        from jarvis.agent_chat.store import AgentChatStore

        self.store = AgentChatStore(db)
        self.sent: list[tuple[str, str]] = []

    def is_running(self, session_id: str) -> bool:
        return False

    def subscribe(self, session_id: str):
        import asyncio

        return asyncio.Queue()

    def unsubscribe(self, session_id: str, q) -> None:
        pass

    async def send(self, session_id: str, text: str, attachments=None) -> str:
        self.sent.append((session_id, text))
        return "turn-1"


def _ctx(utterance: str = "lass Scout das machen") -> SimpleNamespace:
    return SimpleNamespace(trace_id=uuid4(), user_utterance=utterance, config={}, memory_read=None)


@pytest.fixture
async def runtime(tmp_path: Path):
    chat = FakeChat(tmp_path / "agent_chat.db")
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    rt = SocietyRuntime(tmp_path, chat_service=lambda: chat, cfg=lambda: cfg)
    await rt.ensure_started()
    await rt.roster.update("scout", {"provider": "openai", "model": "gpt-5.2"})
    await rt.roster.update("archivist", {"provider": "openai", "model": "gpt-5.2"})
    try:
        yield rt, chat
    finally:
        await rt.close()


async def test_delegate_acks_and_assigns(runtime):
    rt, chat = runtime
    tool = DelegateToAgentTool(runtime_resolver=lambda: rt)
    res = await tool.execute(
        {"agent": "Scout", "task": "Find the best VPS.", "turn_language": "de"}, _ctx()
    )
    assert res.success, res.error
    assert res.output == "Scout ist dran, ich sage Bescheid."  # i18n-allow: spoken ack
    assert res.artifacts == ("agent:scout",)
    # The assignment went through the scheduler into Scout's canonical chat.
    assert chat.sent and chat.sent[0][0] == "society:scout"
    assert "Find the best VPS." in chat.sent[0][1]
    events = await rt.store.events_since(0)
    assert [e.msg_type for e in events][-2:] == [MsgType.ASSIGN, MsgType.CLAIM]
    assert events[-2].from_agent == "jarvis"


async def test_delegate_speaks_english_when_the_turn_is_english(runtime):
    rt, _ = runtime
    tool = DelegateToAgentTool(runtime_resolver=lambda: rt)
    res = await tool.execute(
        {"agent": "scout", "task": "Find it.", "turn_language": "en"}, _ctx("let scout find it")
    )
    assert res.output == "Scout is on it, I will let you know."


async def test_delegate_unknown_agent(runtime):
    rt, _ = runtime
    tool = DelegateToAgentTool(runtime_resolver=lambda: rt)
    res = await tool.execute(
        {"agent": "Nobody", "task": "x", "turn_language": "en"}, _ctx("let nobody do x")
    )
    assert res.success is False and res.error == "target_unknown"
    assert "Nobody" in res.output


async def test_delegate_reads_the_veto_back(runtime):
    rt, _ = runtime
    await rt.store.set_kill_switch(True)
    tool = DelegateToAgentTool(runtime_resolver=lambda: rt)
    res = await tool.execute(
        {"agent": "scout", "task": "x", "turn_language": "en"}, _ctx("let scout do x")
    )
    assert res.success is False and res.error == "kill_switch"
    assert res.output.startswith("Scout cannot take that right now")


async def test_not_ready_paths():
    tool = DelegateToAgentTool(runtime_resolver=lambda: None)
    res = await tool.execute(
        {"agent": "scout", "task": "x", "turn_language": "en"}, _ctx("let scout do x")
    )
    assert res.success is False and res.output == "The agents are not ready yet."
    empty = await tool.execute({"agent": "", "task": ""}, _ctx("x"))
    assert empty.success is False

    def _boom():
        raise RuntimeError("no app")

    status = SocietyStatusTool(runtime_resolver=_boom)
    res = await status.execute({}, _ctx("who is on the team"))
    assert res.success is False


async def test_status_answers_from_the_board(runtime):
    rt, _ = runtime
    status = SocietyStatusTool(runtime_resolver=lambda: rt)
    team = await status.execute({"turn_language": "en"}, _ctx("who is on the team"))
    assert team.output == "Active agents: Scout, Archivist."
    idle = await status.execute(
        {"agent": "archivist", "turn_language": "en"}, _ctx("what is archivist doing")
    )
    assert idle.output == "Archivist has nothing to do right now."
    delegate = DelegateToAgentTool(runtime_resolver=lambda: rt)
    await delegate.execute({"agent": "scout", "task": "Find the best VPS."}, _ctx("let scout"))
    working = await status.execute(
        {"agent": "Scout", "turn_language": "en"}, _ctx("what is scout doing")
    )
    assert working.output.startswith("Scout is working on:")
    unknown = await status.execute({"agent": "ghost"}, _ctx("what is ghost doing"))
    assert unknown.success is False
