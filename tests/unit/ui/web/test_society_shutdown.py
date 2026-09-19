"""Full Society ownership must end after writers drain, including its SQLite thread."""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.society.runtime import SocietyRuntime, current_runtime
from jarvis.ui.web.server import WebServer
from tests.fakes.web_shutdown import (
    DeliveryAfterChatDrain,
    DrainingSocietyHttpServer,
    GatedDelivery,
    GatedMetadataWriter,
    IdleChatRunner,
    RecordingCodingGateway,
    ResistantDelivery,
    SocietyStoreDependent,
)


@pytest.mark.asyncio
async def test_server_shutdown_drains_writers_then_closes_society_database(tmp_path):
    cfg = JarvisConfig()
    cfg.memory.data_dir = str(tmp_path)
    server = WebServer(cfg, bus=EventBus())
    society = SocietyRuntime(tmp_path, seed_starter_team=False)
    server.app.state.society = society
    await society.ensure_started()
    connection = society.store.conn
    # aiosqlite versions expose either a worker attribute or a Thread-like
    # connection. Check the capability instead of an unpinned class identity.
    worker = getattr(connection, "_thread", connection)
    is_alive = getattr(worker, "is_alive", None)
    assert callable(is_alive) and is_alive()
    events: list[str] = []
    server.app.state.swarm = SocietyStoreDependent(society.store, events, "swarm")
    server.app.state.agent_chat = SocietyStoreDependent(society.store, events, "chat")
    server._task_scheduler = SocietyStoreDependent(society.store, events, "tasks")
    server._channel_chat_bridge = SocietyStoreDependent(society.store, events, "channels")
    http = DrainingSocietyHttpServer(society.store, events, "http")
    server._server = http
    server._serve_task = asyncio.create_task(http.serve())
    try:
        await server.stop()
        assert events == ["swarm", "chat", "tasks", "channels", "http"]
        assert server.app.state.society is None
        assert current_runtime() is None
        assert society.store._conn is None
        await asyncio.to_thread(worker.join, 1.0)
        assert not is_alive()
        assert worker not in threading.enumerate()
    finally:
        # A failing regression must not strand the exact non-daemon worker it
        # is diagnosing and hang the test process itself.
        await society.close()


@pytest.mark.asyncio
async def test_server_shutdown_closes_partially_started_society_and_can_repeat(tmp_path):
    cfg = JarvisConfig()
    cfg.memory.data_dir = str(tmp_path)
    server = WebServer(cfg, bus=EventBus())
    society = SocietyRuntime(tmp_path, seed_starter_team=False)
    server.app.state.society = society
    # Startup can stop after opening the store but before _started is set.
    await society.store.open()
    try:
        await server.stop()
        await server.stop()
        assert society.store._conn is None
        assert server.app.state.society is None
    finally:
        await society.close()


@pytest.mark.asyncio
async def test_shutdown_preserves_queued_delivery_without_starting_after_chat_drain(
    tmp_path, monkeypatch
):
    from jarvis.agent_chat import service as service_module
    from jarvis.agent_chat.service import AgentChatService
    from jarvis.agent_chat.store import AgentChatStore
    from jarvis.society.chat_binding import ensure_session, make_deliver_hook
    from jarvis.society.store import SocietyStore

    cfg = JarvisConfig()
    cfg.memory.data_dir = str(tmp_path)
    runner = IdleChatRunner()
    monkeypatch.setattr(service_module, "run_brain_turn", runner)
    service = AgentChatService(AgentChatStore(":memory:"))
    server = WebServer(cfg, bus=EventBus())
    society = SocietyRuntime(
        tmp_path, deliver=make_deliver_hook(lambda: service, lambda: cfg), cfg=lambda: cfg
    )
    server.app.state.society = society
    server.app.state.agent_chat = service
    try:
        await society.ensure_started()
        agent, _ = await society.roster.create(name="Shutdown scout", provider="openai")
        session = ensure_session(service, cfg, agent)
        await service.send(
            session.session_id, "Initial work", control_runner=runner, direct_user=False
        )
        await asyncio.wait_for(runner.started.wait(), timeout=2)
        message = await society.say(from_agent="user", to_agent=agent.agent_id, text="Pending work")
        assert await society.store.delivery_status(message.event_id) == "queued"
        late = DeliveryAfterChatDrain(society, service, session.session_id, message.event_id)
        server._task_scheduler = late
        await server.stop()
        assert late.observed == (False, "queued")
        assert len(runner.starts) == 1 and not service.is_running(session.session_id)
        persisted = SocietyStore(tmp_path / "society.db")
        await persisted.open()
        try:
            assert await persisted.delivery_status(message.event_id) == "queued"
            assert await persisted.get_meta("shutdown-writer") == "finished after chat drain"
        finally:
            await persisted.close()
        # A completed close permits a real reopen; queued work is not lost.
        runner.started.clear()
        await society.ensure_started()
        await asyncio.wait_for(runner.started.wait(), timeout=2)
        assert len(runner.starts) == 2
    finally:
        await society.quiesce()
        await service.cancel_all()
        await society.close()
        service.store.close()


@pytest.mark.asyncio
async def test_quiesce_joins_admitted_delivery_keeps_result_writers_and_defers_reviews(tmp_path):
    from jarvis.society.events import MsgType, SocietyEnvelope

    delivery = GatedDelivery()
    society = SocietyRuntime(tmp_path, deliver=delivery)
    await society.ensure_started()
    # Isolate an admitted bus handler from the separately owned retry producer.
    society._delivery_task.cancel()
    await asyncio.gather(society._delivery_task, return_exceptions=True)
    society._delivery_task = None
    agent, _ = await society.roster.create(name="Quiesce scout", provider="openai")
    publishing = asyncio.create_task(
        society.say(from_agent="user", to_agent=agent.agent_id, text="Already admitted")
    )
    try:
        await asyncio.wait_for(delivery.entered.wait(), timeout=2)
        stopping = asyncio.create_task(society.quiesce())
        await asyncio.sleep(0)
        assert not stopping.done()
        delivery.release.set()
        first = await publishing
        await stopping
        assert delivery.completed == [first.event_id]
        second = await society.say(from_agent="user", to_agent=agent.agent_id, text="Keep queued")
        assert await society.store.delivery_status(second.event_id) == "queued"
        society.scheduler.note_run_started("old-run", agent.agent_id)
        await society.store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.RESULT,
                from_agent=agent.agent_id,
                trace_id="old-trace",
                payload={
                    "run_id": "old-run",
                    "done": "Persisted result",
                    "output": ["result"],
                    "next_owner": agent.agent_id,
                },
            )
        )
        assert society.scheduler.running == {}
        assert delivery.completed == [first.event_id]
        with pytest.raises(RuntimeError, match="shutting down"):
            await society.ensure_started()
        assert not await society.prepare_context()
        society.coding_supervision._ensure_loop()
        assert society.coding_supervision.task is None
        with pytest.raises(RuntimeError, match="shutting down"):
            await society.coding_supervision.assign(agent.agent_id, agent.session_id, {})
        await society.turn_completed(
            SimpleNamespace(session_id=agent.session_id),
            SimpleNamespace(
                events_json=json.dumps(
                    [
                        {
                            "seq": 1,
                            "kind": "user_message",
                            "payload": {"text": "Retain this preference"},
                        },
                        {"seq": 2, "kind": "assistant_text", "payload": {"text": "Understood"}},
                        {"seq": 3, "kind": "turn_finished", "payload": {"status": "done"}},
                    ]
                ),
                turn=SimpleNamespace(turn_id="old-turn", direct_user=True),
            ),
        )
        pending = society.conversations.pending_reviews()
        assert any(item["turn_id"] == "old-turn" for item in pending)
        assert not society._producers
        await society.quiesce()
        assert await society.store.delivery_status(second.event_id) == "queued"
    finally:
        delivery.release.set()
        await asyncio.gather(publishing, return_exceptions=True)
        await society.close()


@pytest.mark.asyncio
async def test_final_close_releases_canceled_turn_slot_before_reopen(tmp_path):
    from jarvis.society.events import MsgType, SocietyEnvelope

    society = SocietyRuntime(tmp_path)
    await society.ensure_started()
    agent, _ = await society.roster.create(name="Retained slot scout", provider="openai")
    removed = []
    service = SimpleNamespace(unsubscribe=lambda session, queue: removed.append(session))
    queue = asyncio.Queue()
    envelope = SocietyEnvelope(
        msg_type=MsgType.ASSIGN,
        from_agent="user",
        to_agent=agent.agent_id,
        trace_id="interrupted-run",
        payload={"text": "Existing work"},
    )
    society.scheduler.note_run_started("old-run", agent.agent_id)
    watcher = asyncio.create_task(
        society._watch_turn(
            service, agent.session_id, queue, "old-turn", "old-run", agent, envelope
        )
    )
    society._watchers.add(watcher)
    try:
        await asyncio.sleep(0)
        await society.quiesce()
        assert not watcher.done() and society.scheduler.active_runs(agent.agent_id) == 1
        await society.close()
        assert watcher.done() and removed == [agent.session_id]
        assert society.scheduler.active_runs(agent.agent_id) == 0
        await society.ensure_started()
        assert society.scheduler.active_runs(agent.agent_id) == 0
    finally:
        await society.close()


@pytest.mark.asyncio
async def test_quiesce_joins_previously_admitted_coding_assignment(tmp_path, monkeypatch):
    service = SimpleNamespace(store=SimpleNamespace(get_session=lambda _: object()))
    society = SocietyRuntime(tmp_path, chat_service=lambda: service)
    await society.ensure_started()
    write = GatedMetadataWriter(society.store.set_meta)
    monkeypatch.setattr(society.store, "set_meta", write)
    gateway = RecordingCodingGateway()
    society._coding_sessions = gateway
    assignment = asyncio.create_task(
        society.coding_supervision.assign(
            "scout",
            "society:scout",
            {
                "workspace_id": "workspace",
                "terminal_id": "pane:one",
                "prompt": "Synthetic assignment",
            },
        )
    )
    try:
        await asyncio.wait_for(write.entered.wait(), timeout=2)
        stopping = asyncio.create_task(society.quiesce())
        await asyncio.sleep(0)
        assert not stopping.done()
        write.release.set()
        result = await assignment
        await stopping
        gateway.quiesced = True
        assert result["supervision"]["state"] == "running"
        assert gateway.sends_after_quiesce == [False]
        assert society.coding_supervision.task is None
        with pytest.raises(RuntimeError, match="shutting down"):
            await society.coding_supervision.assign("scout", "society:scout", {})
    finally:
        write.release.set()
        await asyncio.gather(assignment, return_exceptions=True)
        await society.close()


@pytest.mark.asyncio
async def test_quiesce_cancels_direct_startup_then_closes_partial_store(tmp_path, monkeypatch):
    society = SocietyRuntime(tmp_path)
    entered = asyncio.Event()
    original_open = society.store.open

    async def delayed_open():
        await original_open()
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(society.store, "open", delayed_open)
    starting = asyncio.create_task(society.ensure_started())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        await society.quiesce()
        assert starting.cancelled() and not society._start_tasks
        await society.close()
        assert society.store._conn is None
    finally:
        starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)
        await society.close()


@pytest.mark.asyncio
async def test_failed_quiesce_keeps_storage_open_until_stalled_writer_finishes(
    tmp_path, monkeypatch
):
    from jarvis.society import shutdown

    monkeypatch.setattr(shutdown, "QUIESCE_TIMEOUT_S", 0.03)
    delivery = ResistantDelivery()
    society = SocietyRuntime(tmp_path, deliver=delivery)
    await society.ensure_started()
    society._delivery_task.cancel()
    await asyncio.gather(society._delivery_task, return_exceptions=True)
    society._delivery_task = None
    agent, _ = await society.roster.create(name="Stalled scout", provider="openai")
    publishing = asyncio.create_task(
        society.say(
            from_agent="user",
            to_agent=agent.agent_id,
            text="Admitted before shutdown",
        )
    )
    try:
        await asyncio.wait_for(delivery.entered.wait(), timeout=2)
        with pytest.raises(RuntimeError, match="storage must remain open"):
            await society.quiesce()
        assert society.store._conn is not None and not publishing.done()
        await society.store.set_meta("retained-writer", "not closed prematurely")
        delivery.release.set()
        await publishing
        await society.quiesce()
        assert await society.store.get_meta("retained-writer") == "not closed prematurely"
        await society.close()
        assert society.store._conn is None
    finally:
        delivery.release.set()
        await asyncio.gather(publishing, return_exceptions=True)
        await society.close()


@pytest.mark.asyncio
async def test_shutdown_fences_late_first_swarm_and_society_construction(tmp_path, monkeypatch):
    from jarvis.swarm import runtime as swarm_runtime

    cfg = JarvisConfig()
    cfg.memory.data_dir = str(tmp_path)
    server = WebServer(cfg, bus=EventBus())
    constructions = []

    def forbidden_construction(*args, **kwargs):
        constructions.append(True)
        raise AssertionError("Late shutdown request constructed a fresh Swarm")

    monkeypatch.setattr(swarm_runtime, "build_service", forbidden_construction)
    await server.stop()
    assert server._shutdown_complete
    with pytest.raises(RuntimeError, match="shutting down"):
        server._build_swarm_runtime()
    with pytest.raises(RuntimeError, match="shutting down"):
        server._build_society_runtime()
    assert constructions == []
    assert server.app.state.swarm is None and server.app.state.society is None


@pytest.mark.asyncio
async def test_server_cannot_restart_while_previous_shutdown_is_incomplete(tmp_path):
    cfg = JarvisConfig()
    cfg.memory.data_dir = str(tmp_path)
    server = WebServer(cfg, bus=EventBus())
    server._stopping = True
    try:
        with pytest.raises(RuntimeError, match="shutdown is incomplete"):
            await server.start(start_serving=False)
        assert server._stopping and not server._shutdown_complete
    finally:
        await server.stop()
