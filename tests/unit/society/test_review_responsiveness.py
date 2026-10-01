"""Slow review seat construction must not stall the desktop event loop."""

import asyncio
import contextvars
import threading
from types import SimpleNamespace

import pytest

from jarvis.society.review import _ask
from tests.fakes.fake_seat_registry import RecordingRegistry


@pytest.fixture
def no_agent_key(monkeypatch):
    from jarvis.core import config

    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: None)


@pytest.mark.asyncio
async def test_review_seat_construction_keeps_loop_responsive(monkeypatch, no_agent_key):
    from jarvis.brain import resolver

    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    entered = asyncio.Event()
    release = threading.Event()
    context = contextvars.ContextVar("review-test-context", default="missing")
    context.set("inherited")

    def slow_build(name):
        assert threading.get_ident() != loop_thread
        assert context.get() == "inherited"
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=3), "event loop could not release seat construction"

    registry = RecordingRegistry(["openai"], on_instantiate=slow_build)
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)
    runtime = SimpleNamespace(config=lambda: None, skills_for=lambda agent_id: None)
    agent = SimpleNamespace(provider="openai", model="test-model", effort="low", agent_id="test")
    task = asyncio.create_task(_ask(runtime, agent, "Evidence"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        # This callback runs while synchronous seat construction is blocked.
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    assert await asyncio.wait_for(task, timeout=2) == {"memories": [], "skill": None}
    assert registry.built_names() == ["openai"]


@pytest.mark.asyncio
async def test_unavailable_review_seat_finishes_without_stop_iteration(monkeypatch, no_agent_key):
    from jarvis.brain import resolver

    registry = RecordingRegistry(["openai", "gemini"])
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)
    runtime = SimpleNamespace(config=lambda: None, skills_for=lambda agent_id: None)
    # A coding CLI with no background brain in this app: nothing can answer.
    agent = SimpleNamespace(provider="cursor", model="", effort="low", agent_id="test")
    assert await asyncio.wait_for(_ask(runtime, agent, "Evidence"), timeout=2) is None
    assert registry.built == []
