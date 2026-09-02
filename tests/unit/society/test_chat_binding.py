"""Binding an agent to its canonical chat; delivering board envelopes into it."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_chat.store import SURFACES, AgentChatStore
from jarvis.society.chat_binding import ensure_session, frame_incoming, make_deliver_hook
from jarvis.society.events import MsgType, SocietyEnvelope
from jarvis.society.runtime import SocietyRuntime


class FakeService:
    def __init__(self, store: AgentChatStore) -> None:
        self.store = store
        self.sent: list[tuple[str, str]] = []
        self.busy: set[str] = set()

    def is_running(self, session_id: str) -> bool:
        return session_id in self.busy

    async def send(self, session_id: str, text: str, attachments=None) -> str:
        self.sent.append((session_id, text))
        return "turn-1"


@pytest.fixture
async def world(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path)
    await runtime.ensure_started()
    svc = FakeService(AgentChatStore(tmp_path / "agent_chat.db"))
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    try:
        yield runtime, svc, cfg
    finally:
        await runtime.close()


def test_society_is_a_surface():
    assert "society" in SURFACES


async def test_ensure_session_is_deterministic_and_reseats(world):
    rt, svc, cfg = world
    scout, _ = await rt.roster.create(name="Scout", provider="openai", model="gpt-5.2")
    first = ensure_session(svc, cfg, scout)
    assert first.session_id == "society:scout"
    assert first.surface == "society"
    assert first.provider == "openai" and first.model == "gpt-5.2"
    assert first.permission_mode == "accept-edits"
    assert first.title == "Scout"
    assert Path(first.cwd).name == "workspace" and os.path.isdir(first.cwd)  # noqa: ASYNC240

    again = ensure_session(svc, cfg, scout)
    assert again.session_id == first.session_id
    assert len(svc.store.list_sessions(surface="society")) == 1

    moved = await rt.roster.update("scout", {"provider": "gemini", "model": "gemini-3-pro"})
    reseated = ensure_session(svc, cfg, moved)
    assert reseated.provider == "gemini" and reseated.model == "gemini-3-pro"
    assert reseated.session_id == "society:scout"


async def test_ceiling_maps_to_stance(world):
    rt, svc, cfg = world
    safe, _ = await rt.roster.create(name="Reader", provider="openai", permission_ceiling="safe")
    ask, _ = await rt.roster.create(name="Asker", provider="openai", permission_ceiling="ask")
    assert ensure_session(svc, cfg, safe).permission_mode == "plan"
    assert ensure_session(svc, cfg, ask).permission_mode == "ask"


async def test_without_provider_the_agents_tier_answers(world, monkeypatch):
    rt, svc, cfg = world
    import jarvis.local_models.assistant_session as tier_mod

    monkeypatch.setattr(
        tier_mod,
        "agents_tier",
        lambda cfg, **kw: SimpleNamespace(provider="grok", model="grok-5", ready=True, reason=""),
    )
    scout, _ = await rt.roster.create(name="Scout")
    session = ensure_session(svc, cfg, scout)
    assert session.provider == "grok" and session.model == "grok-5"

    monkeypatch.setattr(
        tier_mod,
        "agents_tier",
        lambda cfg, **kw: SimpleNamespace(provider="", model="", ready=False, reason="no key"),
    )
    quill, _ = await rt.roster.create(name="Quill")
    with pytest.raises(PermissionError):
        ensure_session(svc, cfg, quill)


async def test_deliver_hook_frames_and_sends(world):
    rt, svc, cfg = world
    scout, _ = await rt.roster.create(name="Scout", provider="openai")
    deliver = make_deliver_hook(lambda: svc, lambda: cfg, resolve_name=lambda a: a.title())
    env = SocietyEnvelope(
        msg_type=MsgType.QUERY,
        from_agent="archivist",
        to_agent="scout",
        trace_id="t",
        payload={"text": "Where is the VPS note?", "refs": ["wiki:society/archivist/vps.md"]},
    )
    await deliver(scout, env)
    assert svc.sent == [
        (
            "society:scout",
            "[query from Archivist]\nWhere is the VPS note?\nRefs: wiki:society/archivist/vps.md",
        )
    ]
    svc.busy.add("society:scout")
    with pytest.raises(RuntimeError, match="target busy"):
        await deliver(scout, env)


def test_result_frame_carries_the_handoff():
    env = SocietyEnvelope(
        msg_type=MsgType.RESULT,
        from_agent="scout",
        to_agent="archivist",
        trace_id="t",
        payload={
            "status": "partial",
            "done": "Found three providers.",
            "output": ["wiki:society/scout/vps.md"],
            "open": ["pricing for the 4 GB tier"],
        },
    )
    text = frame_incoming(env, "Scout")
    assert text.splitlines() == [
        "[result from Scout]",
        "Status: partial",
        "Done: Found three providers.",
        "Output: wiki:society/scout/vps.md",
        "Open: pricing for the 4 GB tier",
    ]


async def test_scheduler_delivers_through_the_hook(world):
    rt, svc, cfg = world
    await rt.roster.create(name="Scout", provider="openai")
    await rt.roster.create(name="Archivist", provider="openai")
    rt.set_deliver(make_deliver_hook(lambda: svc, lambda: cfg))
    await rt.say(from_agent="scout", to_agent="archivist", text="ping")
    assert [s[0] for s in svc.sent] == ["society:archivist"]
    assert svc.sent[0][1].startswith("[say from scout]")


def test_unfiltered_session_list_hides_society_sessions(tmp_path: Path):
    from jarvis.ui.web.agent_chat_routes import router

    store = AgentChatStore(tmp_path / "agent_chat.db")
    store.create_session(provider="openai", model="m", effort="", cwd="", surface="agent")
    store.create_session(
        provider="openai", model="m", effort="", cwd="", surface="society", session_id="society:x"
    )
    app = FastAPI()
    app.include_router(router)
    app.state.agent_chat = SimpleNamespace(store=store, is_running=lambda sid: False)
    with TestClient(app) as c:
        everything = c.get("/api/agent-chat/sessions").json()["sessions"]
        assert [s["surface"] for s in everything] == ["agent"]
        only = c.get("/api/agent-chat/sessions", params={"surface": "society"}).json()["sessions"]
        assert [s["session_id"] for s in only] == ["society:x"]
