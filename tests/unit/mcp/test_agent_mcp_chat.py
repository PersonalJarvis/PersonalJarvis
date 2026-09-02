"""``agent_chat``: the tool that turns an MCP client into a keyboard.

The contract test proves the surface refuses to talk when the kill switch is
on. This one proves the happy path and the shapes around it, against a fake
chat service — no model, no provider, no network.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI

from jarvis.agent_chat.store import AgentChatStore
from jarvis.core import runtime_refs
from jarvis.mcp.agents import tools as agent_tools
from jarvis.society.runtime import SocietyRuntime


class FakeChatService:
    """A chat service that answers a scripted turn on the subscriber queue."""

    def __init__(self, store: AgentChatStore, script: list[dict[str, Any]] | None = None) -> None:
        self.store = store
        self.sent: list[tuple[str, str]] = []
        self.busy: set[str] = set()
        self._subs: dict[str, set[asyncio.Queue[Any]]] = {}
        self._script = script

    def is_running(self, session_id: str) -> bool:
        return session_id in self.busy

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]:
        q: asyncio.Queue[Any] = asyncio.Queue()
        self._subs.setdefault(session_id, set()).add(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue[Any]) -> None:
        self._subs.get(session_id, set()).discard(q)

    async def send(self, session_id: str, text: str, attachments: Any = None) -> str:
        self.sent.append((session_id, text))
        turn_id = f"turn-{len(self.sent)}"
        events = self._script if self._script is not None else _default_script(text)
        for event in events:
            payload = dict(event.get("payload") or {})
            payload.setdefault("turn_id", turn_id)
            for q in self._subs.get(session_id, set()):
                q.put_nowait({"kind": event["kind"], "payload": payload})
        return turn_id


def _default_script(text: str) -> list[dict[str, Any]]:
    return [
        {"kind": "tool_call", "payload": {"name": "wiki-recall"}},
        {"kind": "assistant_text", "payload": {"text": f"Heard you: {text}"}},
        {"kind": "turn_finished", "payload": {"status": "ok"}},
    ]


@pytest.fixture
async def world(tmp_path: Path):
    """A society whose agents have a working (fake) chat behind them."""
    svc = FakeChatService(AgentChatStore(tmp_path / "agent_chat.db"))
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    runtime = SocietyRuntime(
        tmp_path,
        seed_starter_team=False,
        chat_service=lambda: svc,
        cfg=lambda: cfg,
    )
    await runtime.ensure_started()
    app = FastAPI()
    app.state.society = runtime
    app.state.society_factory = lambda: runtime
    runtime_refs.set_web_app(app)
    try:
        yield runtime, svc
    finally:
        runtime_refs._reset_for_tests()
        await runtime.close()


async def _call(tool: str, **args: Any) -> Any:
    text = await agent_tools.call(tool, args)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


async def test_a_client_writes_to_an_agent_and_gets_the_answer(world) -> None:
    rt, svc = world
    await rt.roster.create(
        name="Scout", title="Scout", description="Finds things.", provider="openai", model="gpt-5.2"
    )

    answer = await _call("agent_chat", agent="Scout", text="What is on the board?")

    assert answer["status"] == "answered"
    assert answer["reply"] == "Heard you: What is on the board?"
    assert answer["tool_calls"] == ["wiki-recall"]
    assert answer["agent_name"] == "Scout"
    assert answer["session_id"] == "society:scout"
    # The message reached the agent's OWN canonical session, not a fresh one.
    assert svc.sent == [("society:scout", "What is on the board?")]


async def test_a_busy_agent_says_so_instead_of_queueing(world) -> None:
    rt, svc = world
    await rt.roster.create(
        name="Scout", title="Scout", description="Finds things.", provider="openai", model="gpt-5.2"
    )
    # Seat the session, then mark it mid-turn.
    await _call("agent_chat", agent="Scout", text="first")
    svc.busy.add("society:scout")

    refused = await agent_tools.call("agent_chat", {"agent": "Scout", "text": "second"})

    assert "middle of a turn" in refused
    assert len(svc.sent) == 1, "a busy agent must not be sent a second message"


async def test_a_blocked_turn_reports_the_reason(tmp_path: Path) -> None:
    svc = FakeChatService(
        AgentChatStore(tmp_path / "agent_chat.db"),
        script=[
            {"kind": "assistant_text", "payload": {"text": "partial"}},
            {"kind": "error", "payload": {"message": "provider refused"}},
        ],
    )
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    runtime = SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: svc, cfg=lambda: cfg
    )
    await runtime.ensure_started()
    app = FastAPI()
    app.state.society = runtime
    app.state.society_factory = lambda: runtime
    runtime_refs.set_web_app(app)
    try:
        await runtime.roster.create(
            name="Scout",
            title="Scout",
            description="Finds things.",
            provider="openai",
            model="gpt-5.2",
        )
        answer = await _call("agent_chat", agent="Scout", text="hello")
        assert answer["status"] == "blocked"
        assert answer["error"] == "provider refused"
        assert answer["reply"] == "partial", "what was said before the failure is kept"
    finally:
        runtime_refs._reset_for_tests()
        await runtime.close()


async def test_a_slow_turn_returns_still_running_rather_than_hanging(tmp_path: Path) -> None:
    """A conversation that outlives the wait hands back a way to follow it."""
    svc = FakeChatService(AgentChatStore(tmp_path / "agent_chat.db"), script=[])
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    runtime = SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: svc, cfg=lambda: cfg
    )
    await runtime.ensure_started()
    app = FastAPI()
    app.state.society = runtime
    app.state.society_factory = lambda: runtime
    runtime_refs.set_web_app(app)
    try:
        await runtime.roster.create(
            name="Scout",
            title="Scout",
            description="Finds things.",
            provider="openai",
            model="gpt-5.2",
        )
        answer = await _call("agent_chat", agent="Scout", text="think hard", timeout_s=0.2)
        assert answer["status"] == "still_running"
        assert "agent_inbox" in answer["note"]
    finally:
        runtime_refs._reset_for_tests()
        await runtime.close()


async def test_chat_without_a_service_refuses_readably(tmp_path: Path) -> None:
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    await runtime.ensure_started()
    app = FastAPI()
    app.state.society = runtime
    app.state.society_factory = lambda: runtime
    runtime_refs.set_web_app(app)
    try:
        await runtime.roster.create(
            name="Scout",
            title="Scout",
            description="Finds things.",
            provider="openai",
            model="gpt-5.2",
        )
        refused = await agent_tools.call("agent_chat", {"agent": "Scout", "text": "hi"})
        assert "chat service is not running" in refused
    finally:
        runtime_refs._reset_for_tests()
        await runtime.close()
