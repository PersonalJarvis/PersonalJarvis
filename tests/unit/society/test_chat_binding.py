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
    from jarvis.agent_chat.service import AgentChatService

    receive_message = AgentChatService.receive_message
    message_status = AgentChatService.message_status

    async def _emit(self, session_id, event):
        self.store.append_event(session_id, event)

    async def post_notice(self, session_id, payload):
        from jarvis.agent_chat.events import make_event

        await self._emit(session_id, make_event("notice", payload))

    async def cancel(self, _session_id):
        return False

    async def bind_society_session(self, session_id):
        from jarvis.society.chat_binding import bind_society_session

        return await bind_society_session(self, session_id)

    def __init__(self, store: AgentChatStore) -> None:
        self.store = store
        self.sent: list[tuple[str, str]] = []
        self.busy: set[str] = set()

    def is_running(self, session_id: str) -> bool:
        return session_id in self.busy

    async def send(self, session_id: str, text: str, attachments=None, *, incoming=None) -> str:
        self.sent.append((session_id, text))
        return "turn-1"


@pytest.fixture
async def world(tmp_path: Path):
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False, cfg=lambda: cfg)
    await runtime.ensure_started()
    svc = FakeService(AgentChatStore(tmp_path / "agent_chat.db"))
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
    assert Path(first.cwd).is_absolute()

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


async def test_existing_session_follows_roster_permissions_name_and_workspace(world):
    rt, svc, cfg = world
    agent, _ = await rt.roster.create(name="Scout", provider="openai")
    first = ensure_session(svc, cfg, agent)
    updated = await rt.roster.update(
        agent.agent_id,
        {
            "name": "Research Scout",
            "permission_ceiling": "safe",
            "workspace_dir": "society/scout/research",
        },
    )

    session = ensure_session(svc, cfg, updated)
    assert session.session_id == first.session_id
    assert session.permission_mode == "plan"
    assert session.title == "Research Scout"
    assert Path(session.cwd).name == "research"
    assert len(svc.store.list_sessions(surface="society")) == 1


async def test_rebinding_preserves_a_more_restrictive_user_stance(world):
    rt, svc, cfg = world
    agent, _ = await rt.roster.create(name="Scout", provider="openai")
    first = ensure_session(svc, cfg, agent)
    assert first.permission_mode == "accept-edits"

    svc.store.update_session(first.session_id, permission_mode="plan")
    svc.store.set_permission_override(first.session_id, "plan")
    assert ensure_session(svc, cfg, agent).permission_mode == "plan"

    svc.store.update_session(first.session_id, permission_mode="ask")
    svc.store.set_permission_override(first.session_id, "ask")
    assert ensure_session(svc, cfg, agent).permission_mode == "ask"


async def test_relaxed_roster_ceiling_restores_default_without_user_override(world):
    rt, svc, cfg = world
    agent, _ = await rt.roster.create(name="Reader", provider="openai", permission_ceiling="safe")
    first = ensure_session(svc, cfg, agent)
    assert first.permission_mode == "plan"

    updated = await rt.roster.update(agent.agent_id, {"permission_ceiling": "monitor"})
    assert ensure_session(svc, cfg, updated).permission_mode == "accept-edits"

    svc.store.set_permission_override(first.session_id, "plan")
    assert ensure_session(svc, cfg, updated).permission_mode == "plan"


async def test_chat_route_records_an_explicit_permission_choice(world):
    from jarvis.ui.web.agent_chat_routes import router

    rt, svc, cfg = world
    agent, _ = await rt.roster.create(name="Scout", provider="openai")
    session = ensure_session(svc, cfg, agent)
    svc.controls = SimpleNamespace(state=lambda _sid: SimpleNamespace(goal=None))
    app = FastAPI()
    app.include_router(router)
    app.state.agent_chat = svc

    with TestClient(app) as client:
        response = client.patch(
            f"/api/agent-chat/sessions/{session.session_id}",
            json={"permission_mode": "ask"},
        )
        stale_model = client.patch(
            f"/api/agent-chat/sessions/{session.session_id}",
            json={"model": "not-the-roster-model"},
        )
    assert response.status_code == 200
    assert svc.store.permission_override(session.session_id) == "ask"
    assert ensure_session(svc, cfg, agent).permission_mode == "ask"
    assert stale_model.status_code == 200
    assert stale_model.json()["model"] == agent.model
    updates = [
        event["payload"]
        for event in svc.store.list_events(session.session_id)
        if event["kind"] == "session_updated"
    ]
    assert {"model": agent.model} in updates


async def test_safe_agent_route_clamps_a_requested_bypass(world):
    from jarvis.ui.web.agent_chat_routes import router

    rt, svc, cfg = world
    agent, _ = await rt.roster.create(name="Reader", provider="openai", permission_ceiling="safe")
    session = ensure_session(svc, cfg, agent)
    svc.controls = SimpleNamespace(state=lambda _sid: SimpleNamespace(goal=None))
    app = FastAPI()
    app.include_router(router)
    app.state.agent_chat = svc

    with TestClient(app) as client:
        response = client.patch(
            f"/api/agent-chat/sessions/{session.session_id}",
            json={"permission_mode": "bypass"},
        )
    assert response.status_code == 200
    assert response.json()["permission_mode"] == "plan"
    assert svc.store.get_session(session.session_id).permission_mode == "plan"
    assert any(
        event["kind"] == "session_updated" and event["payload"].get("permission_mode") == "plan"
        for event in svc.store.list_events(session.session_id)
    )


async def test_society_patch_does_not_reseat_if_a_turn_starts_during_cleanup(world):
    from jarvis.ui.web.agent_chat_routes import router

    rt, svc, cfg = world
    agent, _ = await rt.roster.create(name="Scout", provider="openai")
    session = ensure_session(svc, cfg, agent)

    async def begin_turn(_session_id):
        svc.busy.add(session.session_id)

    svc.controls = SimpleNamespace(
        state=lambda _sid: SimpleNamespace(goal=None),
        _clear_saved_native=begin_turn,
    )
    app = FastAPI()
    app.include_router(router)
    app.state.agent_chat = svc
    with TestClient(app) as client:
        response = client.patch(
            f"/api/agent-chat/sessions/{session.session_id}",
            json={"provider": "gemini"},
        )
    assert response.status_code == 409
    assert svc.store.get_session(session.session_id).provider == "openai"


async def test_direct_send_rebinds_safe_agent_before_runner(tmp_path: Path):
    from jarvis.agent_chat.service import AgentChatService

    store = AgentChatStore(tmp_path / "agent_chat.db")
    svc = AgentChatService(store, assistant_name=lambda: "Test")
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    rt = SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: svc, cfg=lambda: cfg
    )
    await rt.ensure_started()
    try:
        agent, _ = await rt.roster.create(
            name="Reader", provider="openai", permission_ceiling="safe"
        )
        session = ensure_session(svc, cfg, agent)
        store.update_session(session.session_id, permission_mode="bypass")
        store.set_permission_override(session.session_id, "bypass")
        observed: list[str] = []

        async def capture(handle, _text):
            observed.append(handle.session.permission_mode)

        await svc.send(
            session.session_id,
            "Read the note",
            control_runner=capture,
            control_owned=True,
            direct_user=False,
        )
        await svc.wait_turn(session.session_id)
        assert observed == ["plan"]
        assert store.get_session(session.session_id).permission_mode == "plan"
    finally:
        await svc.cancel_all()
        await rt.close()


async def test_busy_turn_defers_provider_reseat_until_it_finishes(tmp_path: Path):
    import asyncio

    from jarvis.agent_chat.control_types import CommandRequest
    from jarvis.agent_chat.service import AgentChatService, SessionBusy

    store = AgentChatStore(tmp_path / "agent_chat.db")
    svc = AgentChatService(store, assistant_name=lambda: "Test")
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    rt = SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: svc, cfg=lambda: cfg
    )
    await rt.ensure_started()
    started = asyncio.Event()
    release = asyncio.Event()
    try:
        agent, _ = await rt.roster.create(name="Scout", provider="openai", model="old")
        session = ensure_session(svc, cfg, agent)

        async def held_runner(_handle, _text):
            started.set()
            await release.wait()
            return "old-vendor-session"

        await svc.send(
            session.session_id,
            "First task",
            control_runner=held_runner,
            control_owned=True,
            direct_user=False,
        )
        await asyncio.wait_for(started.wait(), timeout=3)
        await rt.roster.update(agent.agent_id, {"provider": "gemini", "model": "new"})
        with pytest.raises(SessionBusy):
            await svc.send(session.session_id, "Second task")
        command = await svc.controls.execute(
            session.session_id, CommandRequest(command="status", request_id="busy-status")
        )
        assert command.status == "done"
        assert command.data["running"] is True
        assert store.get_session(session.session_id).provider == "openai"

        release.set()
        await svc.wait_turn(session.session_id)
        assert store.get_session(session.session_id).vendor_session == "old-vendor-session"
        moved = await svc.bind_society_session(session.session_id)
        assert moved.provider == "gemini" and moved.model == "new"
        assert moved.vendor_session == ""
    finally:
        release.set()
        await svc.cancel_all()
        await rt.close()


async def test_build_cannot_raise_a_safe_agent_above_its_ceiling(world):
    from jarvis.agent_chat.control import ChatControls
    from jarvis.agent_chat.control_types import CommandRequest

    rt, svc, cfg = world
    agent, _ = await rt.roster.create(name="Scout", provider="openai")
    session = ensure_session(svc, cfg, agent)
    controls = ChatControls(svc, adapters=[])
    svc.controls = controls
    plan = await controls.execute(
        session.session_id, CommandRequest(command="plan", request_id="plan-1")
    )
    assert plan.status == "done"
    updated = await rt.roster.update(agent.agent_id, {"permission_ceiling": "safe"})
    assert updated.permission_ceiling == "safe"
    build = await controls.execute(
        session.session_id, CommandRequest(command="build", request_id="build-1")
    )
    assert build.status == "failed"
    assert svc.store.get_session(session.session_id).permission_mode == "plan"


async def test_build_sees_a_relaxed_roster_ceiling_before_checking_plan(world):
    from jarvis.agent_chat.control import ChatControls
    from jarvis.agent_chat.control_types import CommandRequest

    rt, svc, cfg = world
    agent, _ = await rt.roster.create(name="Reader", provider="openai", permission_ceiling="safe")
    session = ensure_session(svc, cfg, agent)
    controls = ChatControls(svc, adapters=[])
    svc.controls = controls
    await rt.roster.update(agent.agent_id, {"permission_ceiling": "monitor"})

    result = await controls.execute(
        session.session_id, CommandRequest(command="build", request_id="build-after-roster")
    )
    assert result.status == "done"
    assert svc.store.get_session(session.session_id).permission_mode == "accept-edits"


async def test_provider_change_with_default_model_discards_old_provider_model(world):
    rt, svc, cfg = world
    agent, _ = await rt.roster.create(name="Scout", provider="openai", model="gpt-5.2")
    first = ensure_session(svc, cfg, agent)
    assert first.model == "gpt-5.2"

    updated = await rt.roster.update(agent.agent_id, {"provider": "gemini", "model": ""})
    session = ensure_session(svc, cfg, updated)
    assert session.provider == "gemini"
    assert session.model == ""
    assert session.session_id == first.session_id


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
            "[query from Archivist]\nWhere is the VPS note?\nRefs: wiki:society/archivist/vps.md\n"
            f"Message id: {env.event_id}; sender id: archivist\n"
            "Reply to the sender using society_message_agent with kind 'answer'. "
            "Include the actual findings or decision; use reply_status=blocked if you "
            "cannot answer. This is internal communication; do not use an external "
            "messaging connector. No preliminary acknowledgement is needed.",
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
    assert text.splitlines()[:5] == [
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
    assert svc.sent[0][1].startswith("[say from Scout]")


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


class FakeTurnService(FakeService):
    """A service whose turns end: the watcher must write a RESULT and free the slot."""

    def __init__(self, store: AgentChatStore) -> None:
        super().__init__(store)
        self.queues: dict[str, list] = {}

    def subscribe(self, session_id: str):
        import asyncio

        q = asyncio.Queue()
        self.queues.setdefault(session_id, []).append(q)
        return q

    def unsubscribe(self, session_id: str, q) -> None:
        self.queues.get(session_id, []).remove(q)

    async def send(self, session_id: str, text: str, attachments=None, *, incoming=None) -> str:
        self.sent.append((session_id, text))
        return "turn-1"

    async def finish(self, session_id: str, text: str, *, status: str = "ok") -> None:
        for q in list(self.queues.get(session_id, [])):
            q.put_nowait({"kind": "assistant_text", "payload": {"turn_id": "turn-1", "text": text}})
            q.put_nowait(
                {"kind": "turn_finished", "payload": {"turn_id": "turn-1", "status": status}}
            )


async def test_assign_runs_in_the_canonical_chat_and_ends_as_a_result(tmp_path: Path):
    import asyncio

    svc = FakeTurnService(AgentChatStore(tmp_path / "agent_chat.db"))
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    rt = SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: svc, cfg=lambda: cfg
    )
    await rt.ensure_started()
    try:
        await rt.roster.create(name="Scout", provider="openai", model="gpt-5.2")
        env = await rt.say(
            from_agent="user", to_agent="scout", text="Find the best VPS.", msg_type=MsgType.ASSIGN
        )
        # The assignment became a framed chat turn on Scout's own session.
        assert svc.sent[0][0] == "society:scout"
        assert svc.sent[0][1].startswith("[assignment from the user]\nFind the best VPS.")
        assert "handoff" in svc.sent[0][1]
        assert rt.scheduler.running == {"turn:turn-1": "scout"}
        await svc.finish("society:scout", "Hetzner CX22 wins. Done.")
        await asyncio.sleep(0.05)
        assert rt.scheduler.running == {}
        thread = await rt.store.events_for_trace(env.trace_id)
        assert [e.msg_type for e in thread] == [MsgType.ASSIGN, MsgType.CLAIM, MsgType.RESULT]
        result = thread[-1]
        assert result.from_agent == "scout" and result.payload["status"] == "done"
        assert result.payload["done"] == "Hetzner CX22 wins. Done."
        assert result.payload["output"] == ["chat:society:scout"]
    finally:
        await rt.close()


async def test_failed_turn_becomes_a_blocked_result(tmp_path: Path):
    import asyncio

    svc = FakeTurnService(AgentChatStore(tmp_path / "agent_chat.db"))
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    rt = SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: svc, cfg=lambda: cfg
    )
    await rt.ensure_started()
    try:
        await rt.roster.create(name="Scout", provider="openai")
        env = await rt.say(from_agent="user", to_agent="scout", text="x", msg_type=MsgType.ASSIGN)
        await svc.finish("society:scout", "", status="error")
        await asyncio.sleep(0.05)
        result = (await rt.store.events_for_trace(env.trace_id))[-1]
        assert result.msg_type is MsgType.RESULT and result.payload["status"] == "blocked"
        assert result.payload["open"]
    finally:
        await rt.close()


async def test_empty_successful_turn_does_not_claim_task_completion(tmp_path: Path):
    import asyncio

    svc = FakeTurnService(AgentChatStore(tmp_path / "agent_chat.db"))
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    rt = SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: svc, cfg=lambda: cfg
    )
    await rt.ensure_started()
    try:
        await rt.roster.create(name="Scout", provider="openai")
        env = await rt.say(from_agent="user", to_agent="scout", text="x", msg_type=MsgType.ASSIGN)
        await svc.finish("society:scout", "")
        await asyncio.sleep(0.05)
        result = (await rt.store.events_for_trace(env.trace_id))[-1]
        assert result.msg_type is MsgType.RESULT
        assert result.payload["status"] == "blocked"
        assert result.payload["open"] == ["Agent finished without a result report."]
    finally:
        await rt.close()
