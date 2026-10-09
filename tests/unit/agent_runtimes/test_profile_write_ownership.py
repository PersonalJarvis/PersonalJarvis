"""Runtime profile writers cannot outlive the turn slot that owns them."""

import asyncio
import threading

import pytest

from jarvis.agent_runtimes.base import TurnSlots, finish_profile_write


@pytest.mark.parametrize("fail", [False, True])
async def test_cancelled_writer_keeps_slot_until_thread_finishes(fail):
    slots = TurnSlots()
    entered, finish = threading.Event(), threading.Event()
    writes = []

    def old_writer():
        entered.set()
        assert finish.wait(5)
        writes.append("old")
        if fail:
            raise OSError("scripted failure")

    async def first_turn():
        release = await slots.acquire("agent")
        try:
            await finish_profile_write(old_writer)
        finally:
            release()

    async def next_turn():
        release = await slots.acquire("agent")
        try:
            writes.append("new")
        finally:
            release()

    first = asyncio.create_task(first_turn())
    second = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        first.cancel()
        await asyncio.sleep(0)
        first.cancel()  # Repeated cancellation must not bypass ownership.
        second = asyncio.create_task(next_turn())
        await asyncio.sleep(0)
        assert slots.busy() and writes == [] and not second.done()
    finally:
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        if second is not None:
            await second
    assert writes == ["old", "new"] and not slots.busy()


async def test_hermes_rejects_unsupported_context_before_startup(monkeypatch, tmp_path):
    from jarvis.agent_runtimes.base import RuntimeTurn, RuntimeUnavailable
    from jarvis.agent_runtimes.hermes import HermesRuntime
    from jarvis.agent_runtimes.model_map import ModelRoute

    runtime = HermesRuntime()

    def unexpected():
        pytest.fail("Incompatible context must be rejected before runtime startup")

    monkeypatch.setattr(runtime, "detect", unexpected)
    route = ModelRoute("ollama", "model", "http://localhost/v1", "chat_completions", None,
                       context_window=8192)
    turn = RuntimeTurn("agent", "Agent", "society:agent", tmp_path, route, None, False)
    with pytest.raises(RuntimeUnavailable, match="64,000"):
        await runtime.launch(turn)
    assert turn.route.context_window == 8192
