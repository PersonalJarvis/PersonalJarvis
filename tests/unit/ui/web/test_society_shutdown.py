"""The server must release the real roster database before its event loop exits."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException, Request

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.society.runtime import SocietyRuntime, SocietyRuntimeClosed, current_runtime
from jarvis.ui.web.society_routes import list_agents
from tests.fakes.fake_society_shutdown import society_shutdown_server


async def start_roster(tmp_path):
    runtime = SocietyRuntime(tmp_path)
    server, cleaned = society_shutdown_server(runtime)
    response = await list_agents(Request({"type": "http", "app": server.app}))
    assert response["agents"]
    connection = runtime.store._conn
    delivery = runtime._delivery_task
    assert connection is not None and delivery is not None
    assert current_runtime() is runtime
    return runtime, server, cleaned, connection, delivery


async def test_server_stop_closes_real_roster_store_and_delivery(tmp_path, monkeypatch):
    runtime, server, cleaned, connection, delivery = await start_roster(tmp_path)
    original_close = runtime.browser.close
    watcher_started = asyncio.Event()

    async def check_close_order():
        assert server.app.state.mars_station_stopping
        assert runtime.store._conn is connection
        cleaned.append("society-browser")
        await original_close()

    async def watch():
        watcher_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.append("society-watcher")

    monkeypatch.setattr(runtime.browser, "close", check_close_order)
    watcher = runtime.background(watch())
    await watcher_started.wait()
    try:
        await asyncio.wait_for(server.stop(), timeout=3)
        assert runtime.store._conn is None
        with pytest.raises(ValueError, match="no active connection"):
            await connection.execute("SELECT 1")
        assert delivery.done()
        assert watcher.done() and not runtime._watchers
        assert cleaned.count("society-browser") == 1
        assert cleaned.index("society-watcher") < cleaned.index("society-browser")
        assert current_runtime() is None
        assert not runtime._started
        assert server.app.state.mars_station_stopping
        assert {"chat", "pty", "mission-approvals"} <= set(cleaned)
    finally:
        # The red test must not itself leak a non-daemon SQLite worker.
        monkeypatch.setattr(runtime.browser, "close", original_close)
        await runtime.close()


@pytest.mark.parametrize("component", ["browser", "coding_supervision"])
async def test_component_failure_still_closes_store_and_finishes_server(
    tmp_path, monkeypatch, component
):
    runtime, server, cleaned, connection, delivery = await start_roster(tmp_path)
    resource = getattr(runtime, component)
    original_close = resource.close

    async def broken_close():
        raise RuntimeError("PRIVATE-COMPONENT-PAYLOAD")

    monkeypatch.setattr(resource, "close", broken_close)
    try:
        with pytest.raises(RuntimeError, match="society_runtime_shutdown_incomplete") as failure:
            await asyncio.wait_for(server.stop(), timeout=3)
        assert "PRIVATE-COMPONENT-PAYLOAD" not in str(failure.value)
        assert runtime.store._conn is None
        with pytest.raises(ValueError, match="no active connection"):
            await connection.execute("SELECT 1")
        assert delivery.done()
        assert current_runtime() is None
        assert {"chat", "pty", "mission-approvals"} <= set(cleaned)
    finally:
        monkeypatch.setattr(resource, "close", original_close)
        await runtime.close()


async def test_cancelled_browser_cleanup_still_releases_roster_store(tmp_path, monkeypatch):
    runtime, _server, _cleaned, connection, delivery = await start_roster(tmp_path)
    original_close = runtime.browser.close
    closing = asyncio.Event()

    async def stalled_close():
        closing.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime.browser, "close", stalled_close)
    task = asyncio.create_task(runtime.close())
    try:
        await asyncio.wait_for(closing.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        assert runtime.store._conn is None
        with pytest.raises(ValueError, match="no active connection"):
            await connection.execute("SELECT 1")
        assert delivery.done()
        assert current_runtime() is None
    finally:
        monkeypatch.setattr(runtime.browser, "close", original_close)
        await runtime.close()


async def test_roster_poll_during_remaining_server_cleanup_cannot_restart(tmp_path, monkeypatch):
    runtime, server, _cleaned, connection, delivery = await start_roster(tmp_path)
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def pending_cleanup():
        cleanup_started.set()
        await release_cleanup.wait()

    monkeypatch.setattr(server, "_stop_marketplace_refresh_scheduler", pending_cleanup)
    stopping = asyncio.create_task(server.stop())
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=2)
        assert not stopping.done()
        assert runtime.store._conn is None
        for _ in range(3):
            with pytest.raises(HTTPException) as error:
                await list_agents(Request({"type": "http", "app": server.app}))
            assert error.value.status_code == 503
            assert await runtime.prepare_context() is False
        assert runtime.store._conn is None
        assert runtime._delivery_task is None and delivery.done()
        assert current_runtime() is None
        with pytest.raises(ValueError, match="no active connection"):
            await connection.execute("SELECT 1")
    finally:
        release_cleanup.set()
        await asyncio.wait_for(stopping, timeout=2)
        await runtime.close()


@pytest.mark.parametrize("pause_at", ["first-await", "remaining-cleanup"])
async def test_shutdown_fences_empty_owner_before_lazy_roster_factory(
    tmp_path, monkeypatch, pause_at
):
    from jarvis.ui.web import mars_routes

    server, _cleaned = society_shutdown_server(None)
    paused = asyncio.Event()
    release = asyncio.Event()
    factory_calls = []

    def factory():
        factory_calls.append("constructed")
        return SocietyRuntime(tmp_path)

    async def pending_cleanup(*_args):
        paused.set()
        await release.wait()

    server.app.state.society_factory = factory
    if pause_at == "first-await":
        monkeypatch.setattr(mars_routes, "stop_mars_station", pending_cleanup)
    else:
        monkeypatch.setattr(server, "_stop_marketplace_refresh_scheduler", pending_cleanup)
    stopping = asyncio.create_task(server.stop())
    try:
        await asyncio.wait_for(paused.wait(), timeout=2)
        assert server.app.state.society_stopping
        with pytest.raises(HTTPException) as error:
            await list_agents(Request({"type": "http", "app": server.app}))
        assert error.value.status_code == 503
        assert error.value.detail == "society runtime stopped"
        assert factory_calls == []
        assert server.app.state.society is None
        assert list(tmp_path.iterdir()) == []
    finally:
        release.set()
        await asyncio.wait_for(stopping, timeout=2)
        if server.app.state.society is not None:
            await server.app.state.society.close()


@pytest.mark.parametrize("existing_owner", [None, object()])
def test_shared_brain_factory_rejects_shutdown_before_owner_lookup(existing_owner):
    server, _cleaned = society_shutdown_server(existing_owner)
    server.app.state.society_stopping = True
    # The guard must run before consulting config or constructing collaborators.
    with pytest.raises(SocietyRuntimeClosed, match="society runtime stopped"):
        server._build_society_runtime()
    assert server.app.state.society is existing_owner


async def test_close_cancels_and_joins_initial_roster_request(tmp_path, monkeypatch):
    runtime = SocietyRuntime(tmp_path)
    server, _cleaned = society_shutdown_server(runtime)
    seeded = asyncio.Event()
    original_seed = runtime.seed_lead

    async def pending_seed():
        await original_seed()
        seeded.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "seed_lead", pending_seed)
    starting = asyncio.create_task(list_agents(Request({"type": "http", "app": server.app})))
    try:
        await asyncio.wait_for(seeded.wait(), timeout=2)
        connection = runtime.store._conn
        assert connection is not None
        await asyncio.wait_for(server.stop(), timeout=3)
        assert starting.done() and starting.cancelled()
        assert runtime._starting_task is None
        assert runtime.store._conn is None
        assert runtime._delivery_task is None
        assert current_runtime() is None
        with pytest.raises(ValueError, match="no active connection"):
            await connection.execute("SELECT 1")
        with pytest.raises(HTTPException) as error:
            await list_agents(Request({"type": "http", "app": server.app}))
        assert error.value.status_code == 503
    finally:
        starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)
        await runtime.close()


async def test_cancel_initial_connect_releases_real_sqlite_worker(tmp_path, monkeypatch):
    import sqlite3

    runtime = SocietyRuntime(tmp_path)
    server, _cleaned = society_shutdown_server(runtime)
    original_connect = sqlite3.connect
    connecting = asyncio.Event()
    release_connect = threading.Event()
    worker_threads = []
    loop = asyncio.get_running_loop()

    def slow_connect(*args, **kwargs):
        worker_threads.append(threading.current_thread())
        loop.call_soon_threadsafe(connecting.set)
        assert release_connect.wait(timeout=5)
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", slow_connect)
    starting = asyncio.create_task(runtime.ensure_started())
    try:
        await asyncio.wait_for(connecting.wait(), timeout=2)
        assert runtime.store._conn is None
        await asyncio.wait_for(server.stop(), timeout=3)
        assert starting.done() and starting.cancelled()
        release_connect.set()
        for worker in worker_threads:
            await asyncio.to_thread(worker.join, 2)
            assert not worker.is_alive()
        assert runtime.store._conn is None
        assert current_runtime() is None
    finally:
        release_connect.set()
        await asyncio.gather(starting, return_exceptions=True)
        await runtime.close()


async def test_slow_startup_keeps_owner_until_it_can_release_store(tmp_path, monkeypatch):
    from jarvis.society import runtime as runtime_module

    runtime = SocietyRuntime(tmp_path)
    seeded = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()
    original_seed = runtime.seed_lead

    async def reluctant_seed():
        await original_seed()
        seeded.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    monkeypatch.setattr(runtime_module, "_CLOSE_TASK_TIMEOUT_S", 0.02)
    monkeypatch.setattr(runtime, "seed_lead", reluctant_seed)
    starting = asyncio.create_task(runtime.ensure_started())
    try:
        await asyncio.wait_for(seeded.wait(), timeout=2)
        with pytest.raises(TimeoutError, match="society task shutdown incomplete"):
            await runtime.close()
        assert cancelled.is_set()
        assert runtime._starting_task is starting and not starting.done()
        assert runtime.store._conn is None
        release.set()
        with pytest.raises(runtime_module.SocietyRuntimeClosed):
            await starting
        assert runtime._starting_task is None
        assert runtime.store._conn is None
        assert runtime._delivery_task is None
        assert current_runtime() is None
    finally:
        release.set()
        await asyncio.gather(starting, return_exceptions=True)
        await runtime.close()


def test_roster_process_exits_after_real_server_stop(tmp_path):
    script = """
import asyncio
import sys
from pathlib import Path
from fastapi import Request
from jarvis.society.runtime import SocietyRuntime
from jarvis.ui.web.society_routes import list_agents
from tests.fakes.fake_society_shutdown import society_shutdown_server

async def main():
    runtime = SocietyRuntime(Path(sys.argv[1]))
    server, _ = society_shutdown_server(runtime)
    await list_agents(Request({'type': 'http', 'app': server.app}))
    await server.stop()
    print('SOCIETY_SERVER_STOP_RETURNED', flush=True)

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[4],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        creationflags=NO_WINDOW_CREATIONFLAGS,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "SOCIETY_SERVER_STOP_RETURNED" in result.stdout
