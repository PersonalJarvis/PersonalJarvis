"""Slow plugin discovery cannot block requests or publish half a tool set."""

import asyncio
import threading

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.brain.tool_surface import reconcile_tool_surface_async


@pytest.mark.asyncio
async def test_refresh_is_off_loop_coalesced_and_published_atomically(monkeypatch):
    manager = BrainManager.__new__(BrainManager)
    manager._tools = {"old": object()}
    manager._local_action_tools = {}
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    builds = []
    applied = []

    def build():
        assert threading.get_ident() != loop_thread
        builds.append(1)
        entered.set()
        assert release.wait(3)
        return {"new": object()}, {"action": object()}

    def apply(snapshot):
        assert threading.get_ident() == loop_thread
        applied.append(snapshot)
        manager._tools, manager._local_action_tools = snapshot

    monkeypatch.setattr(manager, "_build_tool_snapshot", build)
    monkeypatch.setattr(manager, "_apply_tool_snapshot", apply)
    first = asyncio.create_task(manager.refresh_tools_async())
    followers = []
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        followers = [asyncio.create_task(manager.refresh_tools_async()) for _ in range(10)]
        await asyncio.sleep(0.01)
        assert len(builds) == 1
        assert list(manager._tools) == ["old"]
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    finally:
        release.set()
        await asyncio.gather(*followers)
        await manager._tool_refresh_task
    assert len(builds) == 2  # all mid-build events become one follow-up snapshot
    assert len(applied) == 2
    assert list(manager._tools) == ["new"]


@pytest.mark.asyncio
async def test_a_missed_event_is_reconciled_before_the_turn(monkeypatch):
    import jarvis.brain.tool_surface as surface

    manager = BrainManager.__new__(BrainManager)
    manager._tool_surface_fp = frozenset({"old"})
    called = []

    async def refresh():
        called.append(True)

    monkeypatch.setattr(manager, "refresh_tools_async", refresh)
    monkeypatch.setattr(surface, "live_tool_surface_fingerprint", lambda: frozenset({"new"}))
    await reconcile_tool_surface_async(manager)
    assert called == [True]
