"""Building the chat service from a worker thread never deadlocks the app.

Live 2026-10-07 the whole app froze for minutes after boot: a sync route's
worker thread built the service under ``_SERVICE_BUILD_LOCK`` and, while
sealing a restart's turns, asked the event loop for itself; the loop was
blocked on the same lock in the async ``get_catalog`` route. The loop is now
found before the lock is taken.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any

import anyio

import jarvis.agent_chat.service as service_module
from jarvis.ui.web.agent_chat_routes import _service_from_state


def test_an_async_route_waiting_for_a_worker_build_does_not_deadlock(monkeypatch) -> None:
    monkeypatch.setattr(service_module, "_KNOWN_LOOP", None)
    outcome: list[Any] = []

    async def scenario() -> None:
        app_loop = asyncio.get_running_loop()
        entered = asyncio.Event()

        def factory() -> SimpleNamespace:
            app_loop.call_soon_threadsafe(entered.set)
            # Let the loop thread reach the build lock first, as it did live.
            time.sleep(0.1)
            found, _on_loop = service_module._app_loop()
            return SimpleNamespace(loop=found)

        state = SimpleNamespace(agent_chat=None, agent_chat_factory=factory)
        worker = asyncio.ensure_future(anyio.to_thread.run_sync(_service_from_state, state))
        await entered.wait()
        on_loop = _service_from_state(state)  # the async route, on the loop thread
        built = await worker
        outcome.append((built, on_loop, asyncio.get_running_loop()))

    runner = threading.Thread(target=lambda: asyncio.run(scenario()), daemon=True)
    runner.start()
    runner.join(timeout=10)
    assert not runner.is_alive(), "the loop and the service build waited on each other"
    built, on_loop, loop = outcome[0]
    assert built is on_loop
    assert built.loop is loop
