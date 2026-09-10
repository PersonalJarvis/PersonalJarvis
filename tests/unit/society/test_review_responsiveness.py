"""Slow review provider initialization must not stall the desktop event loop."""

import asyncio
import contextvars
import threading
from types import SimpleNamespace

import pytest

from jarvis.core.protocols import BrainDelta
from jarvis.society.review import _ask


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_review_provider_resolution_keeps_loop_responsive(monkeypatch, fallback):
    from jarvis.agent_chat import runner_brain
    from jarvis.core import config
    from jarvis.society import learning

    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    entered = asyncio.Event()
    release = threading.Event()
    context = contextvars.ContextVar("review-test-context", default="missing")
    context.set("inherited")
    calls = []

    class Provider:
        def __init__(self, name):
            self.name = name
            self._model = "test-model"

        async def complete(self, request):
            assert threading.get_ident() == loop_thread
            if self.name == "primary" and fallback:
                raise RuntimeError("primary unavailable")
            yield BrainDelta(content='{"memories": [], "skill": null}')

    def getter(name, model, *, scope):
        calls.append((name, scope))
        if name == ("fallback" if fallback else "primary"):
            assert threading.get_ident() != loop_thread
            assert context.get() == "inherited"
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(timeout=3), "event loop could not release provider initialization"
        return Provider(name)

    class Creator:
        def _candidate_brains(self):
            assert threading.get_ident() != loop_thread
            yield Provider("fallback"), "fallback"

    monkeypatch.setattr(runner_brain, "brain_manager", lambda: SimpleNamespace(_get_brain=getter))
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: None)
    monkeypatch.setattr(learning, "default_creator_factory", lambda cfg: lambda *args: Creator())
    runtime = SimpleNamespace(config=lambda: None, skills_for=lambda agent_id: None)
    agent = SimpleNamespace(provider="primary", model="test-model", effort="low", agent_id="test")
    task = asyncio.create_task(_ask(runtime, agent, "Evidence"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        # This callback runs while synchronous provider construction is blocked.
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    assert await asyncio.wait_for(task, timeout=2) == {"memories": [], "skill": None}
    assert calls == [("primary", "society-review:test")] + (
        [("fallback", "society-review:test")] if fallback else []
    )


@pytest.mark.asyncio
async def test_unavailable_review_providers_finish_without_stop_iteration(monkeypatch):
    from jarvis.agent_chat import runner_brain
    from jarvis.core import config
    from jarvis.society import learning

    def unavailable(*args, **kwargs):
        raise RuntimeError("no usable provider")

    monkeypatch.setattr(
        runner_brain, "brain_manager", lambda: SimpleNamespace(_get_brain=unavailable)
    )
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: None)
    monkeypatch.setattr(learning, "default_creator_factory", lambda cfg: lambda *args: None)
    runtime = SimpleNamespace(config=lambda: None, skills_for=lambda agent_id: None)
    agent = SimpleNamespace(provider="primary", model="test", effort="low", agent_id="test")
    assert await asyncio.wait_for(_ask(runtime, agent, "Evidence"), timeout=2) is None
