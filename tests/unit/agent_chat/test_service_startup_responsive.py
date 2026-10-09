"""First chat requests must not block the loop behind a service builder thread."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from jarvis.ui.web import agent_chat_routes as routes


async def test_catalog_waits_for_one_service_without_blocking_the_event_loop(monkeypatch):
    entered = threading.Event()
    finish = threading.Event()
    loop_was_blocked = []
    builds = []
    service = SimpleNamespace(
        default_cwd=lambda _surface: ".",
        store=SimpleNamespace(chat_selection=lambda: None),
    )

    def factory():
        builds.append(True)
        entered.set()
        if not finish.wait(2):
            loop_was_blocked.append(True)
        return service

    async def models():
        return {}

    monkeypatch.setattr(routes, "_live_cli_models", models)
    monkeypatch.setattr(routes, "_catalog_rows", lambda *_args: [])
    state = SimpleNamespace(agent_chat=None, agent_chat_factory=factory)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    owner = asyncio.create_task(asyncio.to_thread(routes._service_from_state, state))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        # Construction needs the loop to make progress. A blocking Lock on
        # this thread deadlocks it until the test's emergency deadline expires.
        asyncio.get_running_loop().call_soon(finish.set)
        result = await routes.get_catalog(request, surface="jarvis")
        assert result["providers"] == []
        assert not loop_was_blocked
        assert len(builds) == 1
        assert await owner is state.agent_chat is service
    finally:
        finish.set()
        await owner


async def test_cancelled_request_does_not_abandon_or_duplicate_service_construction():
    entered = threading.Event()
    finish = threading.Event()
    service = object()
    builds = []

    def factory():
        builds.append(True)
        entered.set()
        assert finish.wait(2)
        return service

    state = SimpleNamespace(agent_chat=None, agent_chat_factory=factory)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    first = asyncio.create_task(routes._async_service(request))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(routes._async_service(request))
        finish.set()
        assert await second is service
        assert state.agent_chat is service and len(builds) == 1
    finally:
        finish.set()
