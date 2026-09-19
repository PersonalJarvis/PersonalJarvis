"""Background runs keep identity, persistence and cancellation separate from chat."""

import asyncio

import pytest

from jarvis.agent_chat.store import AgentChatStore
from jarvis.society.routine_runner import run_owned_routine
from jarvis.society.routines import build_task_spec
from jarvis.tasks.runner import _owner_blocks_fallback
from jarvis.tasks.store import TaskStore
from tests.contract.test_society_continuity import world as world


async def test_persisted_run_links_survive_reopening_without_loading_main_chat(world, tmp_path):
    runtime, service, main, _ = world
    store = TaskStore(tmp_path / "tasks.db")
    await store.init()
    runtime.task_services = lambda: (store, None)
    agent = await runtime.roster.get("mailbox")
    spec = build_task_spec(agent, title="Inbox", prompt="Read mail", schedule={"kind": "every"})
    task_id = str(await store.insert(spec))
    try:
        await run_owned_routine(runtime, task_id, spec.tags, spec.action.prompt)
        await run_owned_routine(runtime, task_id, spec.tags, spec.action.prompt)
        row = await store.get(task_id)
        links = [step["payload"]["session_id"] for step in row["steps"]]
        assert len(set(links)) == 2
        reopened = AgentChatStore(tmp_path / "chat.db")
        try:
            for sid in links:
                session = reopened.get_session(sid)
                assert session.permission_mode == "bypass"
                assert session.provider == main.provider
                assert session.cwd == main.cwd
                assert reopened.list_events(sid)[-1]["kind"] == "turn_finished"
            assert reopened.list_events(main.session_id) == []
            assert reopened.get_session(main.session_id).permission_mode == main.permission_mode
        finally:
            reopened.close()
    finally:
        await store.close()


async def test_busy_main_chat_queues_routine_without_mixing_transcripts(world, monkeypatch):
    runtime, service, main, brain = world
    started = asyncio.Event()
    release = asyncio.Event()
    generate = brain.generate

    async def respond(text, **kwargs):
        if text == "Main conversation":
            started.set()
            await release.wait()
        return await generate(text, **kwargs)

    monkeypatch.setattr(brain, "generate", respond)
    await service.send(main.session_id, "Main conversation")
    await asyncio.wait_for(started.wait(), 2)
    created = asyncio.Event()
    create_session = service.store.create_session

    def record_session(**kwargs):
        session = create_session(**kwargs)
        created.set()
        return session

    monkeypatch.setattr(service.store, "create_session", record_session)
    routine = asyncio.create_task(
        run_owned_routine(runtime, "digest", ("society", "agent:mailbox"), "Read mail")
    )
    try:
        await asyncio.wait_for(created.wait(), 2)
        assert service.is_running(main.session_id)
        release.set()
        assert await asyncio.wait_for(routine, 3) == brain.reply
        messages = [
            e for e in service.store.list_events(main.session_id) if e["kind"] == "user_message"
        ]
        assert [e["payload"]["text"] for e in messages] == ["Main conversation"]
    finally:
        release.set()
        if not routine.done():
            routine.cancel()
            await asyncio.gather(routine, return_exceptions=True)
        await service.cancel(main.session_id)


async def test_failed_run_stays_in_its_chat_and_cannot_fall_back(world, monkeypatch):
    runtime, service, main, brain = world

    async def fail(*args, **kwargs):
        raise RuntimeError("Synthetic provider outage")

    monkeypatch.setattr(brain, "generate", fail)
    with pytest.raises(RuntimeError, match="The routine chat failed") as failure:
        await run_owned_routine(runtime, "digest", ("society", "agent:mailbox"), "Read mail")
    assert _owner_blocks_fallback(failure.value)
    runs = [
        s for s in service.store.list_sessions(surface="society") if ":routine:" in s.session_id
    ]
    assert len(runs) == 1
    assert service.store.list_events(runs[0].session_id)[-1]["payload"]["status"] == "error"
    assert service.store.list_events(main.session_id) == []


@pytest.mark.parametrize("via_chat", [False, True])
async def test_cancelling_routine_preserves_main_chat(world, monkeypatch, via_chat):
    runtime, service, main, brain = world
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold(*args, **kwargs):
        started.set()
        await release.wait()
        return "Finished"

    monkeypatch.setattr(brain, "generate", hold)
    routine = asyncio.create_task(
        run_owned_routine(runtime, "digest", ("society", "agent:mailbox"), "Read mail")
    )
    try:
        await asyncio.wait_for(started.wait(), 2)
        if via_chat:
            session = next(
                s
                for s in service.store.list_sessions(surface="society")
                if ":routine:" in s.session_id
            )
            await service.cancel(session.session_id)
        else:
            routine.cancel()
        with pytest.raises(asyncio.CancelledError):
            await routine
        runs = [
            s for s in service.store.list_sessions(surface="society") if ":routine:" in s.session_id
        ]
        assert len(runs) == 1
        assert not service.is_running(runs[0].session_id)
        assert service.store.list_events(runs[0].session_id)[-1]["payload"]["status"] == "cancelled"
        assert service.store.list_events(main.session_id) == []
    finally:
        release.set()
        if not routine.done():
            routine.cancel()
            await asyncio.gather(routine, return_exceptions=True)
