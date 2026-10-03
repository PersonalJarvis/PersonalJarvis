"""Delegation holds the originating turn, without polling a model or losing races."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from jarvis.agent_chat import delegation_wait
from jarvis.agent_chat import service as service_mod
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.runner_api import messages_from_events
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.core.delegated_work import (
    DelegatedWork,
    collect_delegated_work,
    register_delegated_work,
    register_dispatch,
)
from jarvis.core.protocols import ChatTurn, current_chat_turn


@pytest.mark.parametrize("runner_kind", ["brain", "claude-cli"])
async def test_six_delegates_finish_one_original_turn_after_all_reports(
    tmp_path,
    monkeypatch,
    runner_kind,
):
    monkeypatch.setattr(delegation_wait, "POLL_SECONDS", 0.001)
    store = AgentChatStore(tmp_path / "chat.db")
    svc = AgentChatService(store)
    session = store.create_session(
        provider="openai", model="", effort="", surface="jarvis", cwd=str(tmp_path)
    )
    ready = [False] * 6
    registered = asyncio.Event()
    prompts = []

    async def runner(handle, prompt, *args, **kwargs):
        prompts.append((handle, prompt))
        if len(prompts) == 1:
            for index in range(6):

                async def probe(i=index):
                    return {"status": "done", "report": f"Finding {i}"} if ready[i] else None

                assert register_delegated_work(DelegatedWork(str(index), f"Agent {index}", probe))
            registered.set()
        else:
            assert all(ready)
            assert len(prompts) == 2
            assert all(f"Finding {i}" in prompt for i in range(6))
            assert handle.session.provider == "openai"
            assert handle.turn_id == prompts[0][0].turn_id
            assert handle.continuation
            if runner_kind == "claude-cli":
                assert handle.session.vendor_session == "same-vendor-session"
        await handle.emit(
            make_event(
                "assistant_text",
                {
                    "turn_id": handle.turn_id,
                    "text": "Started" if len(prompts) == 1 else "Audit findings",
                },
            )
        )
        await handle.emit(
            make_event(
                "turn_finished",
                {
                    "turn_id": handle.turn_id,
                    "status": "done",
                    "usage": {"output_tokens": 4},
                    "cost_usd": 0.01,
                },
            )
        )
        if runner_kind == "claude-cli":
            return "same-vendor-session"

    monkeypatch.setattr(service_mod, "run_brain_turn", runner)
    monkeypatch.setattr(service_mod, "run_cli_turn", runner)
    monkeypatch.setattr(service_mod, "resolve_runner", lambda *a, **kw: runner_kind)
    await svc.send(session.session_id, "Audit all six agents and report back.")
    task = svc._running[session.session_id].task
    await asyncio.wait_for(registered.wait(), 2)
    ready[:5] = [True] * 5
    await asyncio.sleep(0.02)
    assert svc.is_running(session.session_id)
    assert len(prompts) == 1
    assert any(
        e["kind"] == "reasoning" and "Waiting for agents" in e["payload"].get("text", "")
        for e in store.list_events(session.session_id)
    )  # A reconnected UI can reconstruct the actual wait from persisted events.
    assert not any(e["kind"] == "turn_finished" for e in store.list_events(session.session_id))
    assert not svc._brain_lock.locked()
    ready[5] = True
    await asyncio.wait_for(task, 3)
    events = store.list_events(session.session_id)
    finishes = [e for e in events if e["kind"] == "turn_finished"]
    assert len(finishes) == 1
    assert finishes[0]["payload"]["usage"] == {"output_tokens": 8}
    assert finishes[0]["payload"]["cost_usd"] == 0.02
    assert len([e for e in events if e["kind"] == "notice"]) == 6
    assert "Finding 5" in str(messages_from_events(events))
    assert not svc.is_running(session.session_id)
    store.close()


async def test_registration_is_exact_turn_scoped_and_survives_mcp_restore():
    parent = ChatTurn("chat", "turn", "request", True, "trace")
    accepted = []

    async def probe():
        return None

    work = DelegatedWork("id", "Agent", probe)
    token = current_chat_turn.set(parent)
    try:
        with collect_delegated_work(accepted.append):
            assert register_delegated_work(work)
            child = current_chat_turn.set(replace(parent, turn_id="child", direct_user=False))
            assert not register_delegated_work(work)
            current_chat_turn.reset(child)
            # HTTP restores the trusted turn rather than inheriting a ContextVar sink.
            restored = current_chat_turn.set(replace(parent))
            assert register_delegated_work(work)
            current_chat_turn.reset(restored)
        assert not register_delegated_work(work)
        assert accepted == [work, work]
    finally:
        current_chat_turn.reset(token)


def handle(events):
    async def emit(event):
        events.append(event)

    return SimpleNamespace(turn_id="t", cancel=asyncio.Event(), emit=emit)


async def test_fast_result_and_duplicate_registration_are_consumed_once():
    events = []
    gate = delegation_wait.DelegationWait(handle(events))

    async def probe():
        return {"status": "done", "report": "already finished"}

    item = DelegatedWork("one", "A", probe)
    gate.add(item)
    gate.add(item)
    assert len(await gate.collect()) == 1
    assert await gate.collect() == []


async def test_cancel_interrupts_slow_evidence_read_and_cleans_up():
    events = []
    gate = delegation_wait.DelegationWait(handle(events))
    reading = asyncio.Event()
    stopped = asyncio.Event()

    async def probe():
        reading.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    gate.add(DelegatedWork("one", "A", probe))
    task = asyncio.create_task(gate.collect())
    await reading.wait()
    gate.handle.cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert stopped.is_set()
    assert not gate.delivered


async def test_unavailable_evidence_retries_then_reports_unknown(monkeypatch):
    monkeypatch.setattr(delegation_wait, "POLL_SECONDS", 0.001)
    gate = delegation_wait.DelegationWait(handle([]))
    calls = 0

    async def probe():
        nonlocal calls
        calls += 1
        raise OSError("evidence unavailable")

    gate.add(DelegatedWork("one", "A", probe))
    rows = await gate.collect()
    assert calls == 3
    assert rows[0]["status"] == "unavailable"


async def test_deadline_is_unresolved_not_success(monkeypatch):
    monkeypatch.setattr(delegation_wait, "WAIT_TIMEOUT_SECONDS", 0)
    gate = delegation_wait.DelegationWait(handle([]))

    async def probe():
        return None

    gate.add(DelegatedWork("one", "A", probe))
    assert (await gate.collect())[0]["status"] == "timed_out"


async def test_active_progress_renews_only_that_jobs_idle_deadline():
    gate = delegation_wait.DelegationWait(handle([]))

    async def probe():
        return {"status": "running", "progress": "new-event"}

    work = DelegatedWork("one", "A", probe)
    gate.add(work)
    gate.deadlines["one"] = 0
    assert await gate._probe(work) is None
    assert gate.deadlines["one"] > 0
    gate.deadlines["one"] = 0
    assert (await gate._probe(work))["status"] == "timed_out"


async def test_delayed_coding_dispatch_registers_jobs_before_wait_can_finish(monkeypatch):
    monkeypatch.setattr(delegation_wait, "POLL_SECONDS", 0.001)
    gate = delegation_wait.DelegationWait(handle([]))
    token = current_chat_turn.set(ChatTurn("chat", "turn", "code", True, "trace"))
    dispatched = asyncio.Event()
    finished = False

    async def dispatch():
        await asyncio.sleep(0.01)

        async def probe():
            return {"status": "completed", "report": "Coding report"} if finished else None

        assert register_delegated_work(DelegatedWork("coding", "Coder", probe))
        dispatched.set()

    try:
        with collect_delegated_work(gate.add):
            task = asyncio.create_task(dispatch())
            register_dispatch(task, ["Coder"])
            wait = asyncio.create_task(gate.collect())
            await dispatched.wait()
            await asyncio.sleep(0.01)
            assert not wait.done()
            finished = True
            rows = await asyncio.wait_for(wait, 1)
            assert [r["status"] for r in rows] == ["dispatched", "completed"]
            await task
    finally:
        current_chat_turn.reset(token)
