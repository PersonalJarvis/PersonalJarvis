"""Registry discovery must settle before its server closes the registries."""

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.ui.web import server as server_module
from tests.fakes.fake_society_shutdown import society_shutdown_server


@pytest.mark.asyncio
async def test_server_joins_slow_registry_bootstraps_before_registry_stop():
    server, cleaned = society_shutdown_server(None)
    started = [asyncio.Event(), asyncio.Event()]
    cancelled = [asyncio.Event(), asyncio.Event()]

    async def bootstrap(index):
        started[index].set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled[index].set()
            # Both owners must receive cancellation before either is joined.
            await cancelled[1 - index].wait()
            cleaned.append(f"bootstrap-{index}")

    async def stop_plugins():
        assert "bootstrap-0" in cleaned and "bootstrap-1" in cleaned
        cleaned.append("plugins-stopped")

    server._cli_registry = SimpleNamespace(bootstrap=lambda: bootstrap(0))
    server._plugin_registry = SimpleNamespace(
        bootstrap=lambda: bootstrap(1), stop=stop_plugins,
    )
    server._schedule_registry_bootstraps()
    tasks = [server._cli_bootstrap_task, server._plugin_bootstrap_task]
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 1)
        await asyncio.wait_for(server.stop(), 2)
        assert all(task.done() for task in tasks)
        assert server._cli_bootstrap_task is None
        assert server._plugin_bootstrap_task is None
        assert "plugins-stopped" in cleaned
    finally:
        for event in cancelled:
            event.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_repeated_boot_scheduling_does_not_replace_live_owner():
    server, _ = society_shutdown_server(None)
    started = asyncio.Event()

    async def bootstrap():
        started.set()
        await asyncio.Event().wait()

    server._cli_registry = SimpleNamespace(bootstrap=bootstrap)
    server._schedule_registry_bootstraps()
    task = server._cli_bootstrap_task
    try:
        await asyncio.wait_for(started.wait(), 1)
        server._schedule_registry_bootstraps()
        assert server._cli_bootstrap_task is task
        assert await server._stop_registry_bootstraps()
        assert task.cancelled()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_pending_bootstrap_keeps_its_locked_registry_for_later_shutdown(monkeypatch):
    monkeypatch.setattr(server_module, "_REGISTRY_BOOTSTRAP_STOP_TIMEOUT_S", 0.01)
    server, cleaned = society_shutdown_server(None)
    server._cli_registry = None
    started = asyncio.Event()
    release = asyncio.Event()
    lock = asyncio.Lock()
    stopped = False

    async def bootstrap():
        async with lock:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await release.wait()

    async def stop_plugins():
        nonlocal stopped
        async with lock:
            stopped = True

    registry = SimpleNamespace(bootstrap=bootstrap, stop=stop_plugins)
    server._plugin_registry = registry
    server._schedule_registry_bootstraps()
    task = server._plugin_bootstrap_task
    try:
        await asyncio.wait_for(started.wait(), 1)
        with pytest.raises(RuntimeError, match="registry_bootstrap_shutdown_incomplete"):
            await asyncio.wait_for(server.stop(), 1)
        assert not stopped
        assert not task.done()
        assert server._plugin_bootstrap_task is task
        assert server._plugin_registry is registry
        assert {"chat", "pty", "mission-approvals"} <= set(cleaned)
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.wait_for(server.stop(), 1)
        assert stopped
        assert server._plugin_registry is None
        assert server._plugin_bootstrap_task is None
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
