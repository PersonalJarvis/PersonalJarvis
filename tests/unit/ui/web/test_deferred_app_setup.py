"""Desktop voice must not wait for unrelated routes or expose a partial app."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import VoiceBootStatus
from jarvis.ui.deferred_setup import DeferredSetup
from jarvis.ui.web.server import WebServer


class BlockingFeatureBuild:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(5), "test did not release feature construction"
        self.finished.set()


async def _wait_for_thread(signal: threading.Event) -> None:
    assert await asyncio.to_thread(signal.wait, 5), "test thread did not reach its barrier"


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_orphan_setup_or_allow_teardown() -> None:
    build = BlockingFeatureBuild()
    owner = DeferredSetup(build)
    waiter = asyncio.create_task(owner.prepare())
    await _wait_for_thread(build.entered)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    stopping = asyncio.create_task(owner.stop())
    await asyncio.sleep(0)
    assert not stopping.done()
    try:
        with pytest.raises(RuntimeError, match="stopping"):
            await owner.prepare()
    finally:
        build.release.set()
        await stopping
    assert build.finished.is_set()
    assert build.calls == 1


@pytest.mark.asyncio
async def test_failed_setup_propagates_and_is_not_replayed() -> None:
    calls = []

    def fail() -> None:
        calls.append("partial installation")
        raise ValueError("feature installation failed")

    owner = DeferredSetup(fail)
    for operation in (owner.prepare, owner.prepare, owner.stop):
        with pytest.raises(ValueError, match="feature installation failed"):
            await operation()
    assert calls == ["partial installation"]


@pytest.mark.asyncio
async def test_concurrent_callers_install_once() -> None:
    calls = []
    owner = DeferredSetup(lambda: calls.append("ready"))
    await asyncio.gather(owner.prepare(), owner.prepare())
    await owner.prepare()
    await owner.stop()
    assert calls == ["ready"]


@pytest.fixture
def core_server(monkeypatch):
    """Use the real core app/readiness wiring without external stores or tools."""
    shared_skills = object()

    def core_routes(self, app):
        @app.get("/api/core")
        def core():
            return {"ok": True}

    def skills(self, app):
        app.state.skill_registry = shared_skills

    monkeypatch.setattr(WebServer, "_register_rest_routes", core_routes)
    monkeypatch.setattr(WebServer, "_setup_skill_registry", skills)
    for name in ("_setup_doc_registry", "_setup_cli_registry", "_setup_plugin_registry"):
        monkeypatch.setattr(WebServer, name, lambda self, app: None)
    monkeypatch.setattr(WebServer, "_schedule_anyio_pool_warm", lambda self: None)
    monkeypatch.setenv("JARVIS_VOICE", "1")
    return shared_skills


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_blocking_feature_work_is_removed_from_voice_constructor_path(
    monkeypatch, core_server, deferred
) -> None:
    """Barrier-based A/B: the same feature work blocks eager, never deferred boot.

    No wall-time assertion: holding the feature barrier represents arbitrary
    route-import cost, making the before/after ordering proof deterministic.
    """
    build = BlockingFeatureBuild()

    def install(self, app):
        build()

        @app.get("/api/feature")
        def feature():
            return {"complete": True}

    monkeypatch.setattr(WebServer, "_finish_app_setup", install)
    bus = EventBus()
    construction = asyncio.create_task(
        asyncio.to_thread(WebServer, JarvisConfig(), bus, defer_feature_routes=deferred)
    )
    preparing = None
    try:
        if not deferred:
            await _wait_for_thread(build.entered)
            assert not construction.done(), "eager constructor must pay feature work"
            build.release.set()
        server = await asyncio.wait_for(construction, 5)
        assert server.bus is bus
        assert server.app.state.skill_registry is core_server
        assert server._voice_ready is False
        if deferred:
            assert not build.entered.is_set()
            assert "/api/feature" not in {r.path for r in server.app.routes}
            # Core readiness ownership exists before optional feature imports.
            await bus.publish(VoiceBootStatus(ready=True, detail="listening"))
            assert server._voice_ready is True
            preparing = asyncio.create_task(server.prepare_app())
            await _wait_for_thread(build.entered)
            assert not preparing.done()
            assert "/api/feature" not in {r.path for r in server.app.routes}
            build.release.set()
            await preparing
            assert server._voice_ready is True
        await server.prepare_app()
        assert "/api/feature" in {r.path for r in server.app.routes}
        assert build.calls == 1
    finally:
        build.release.set()
        await asyncio.gather(
            construction, *([preparing] if preparing else []), return_exceptions=True
        )


@pytest.mark.asyncio
async def test_stop_before_prepare_prevents_feature_side_effects() -> None:
    calls = []
    owner = DeferredSetup(lambda: calls.append("unexpected"))
    await owner.stop()
    with pytest.raises(RuntimeError, match="stopping"):
        await owner.prepare()
    assert calls == []


def test_desktop_shutdown_timeout_keeps_loop_until_builder_and_cleanup_finish(
    monkeypatch, tmp_path
) -> None:
    """Exercise DesktopApp.shutdown, including its bounded cross-thread waits."""
    import sys

    import jarvis.agentic_ide as ide
    from jarvis.core import runtime_refs
    from jarvis.ui import desktop_app as desktop

    build = BlockingFeatureBuild()
    publications = []
    events = []
    stopped = threading.Event()
    closed = threading.Event()
    loop_started = threading.Event()
    loop = asyncio.new_event_loop()

    class Server:
        # Use the production publication fence while replacing external cleanup.
        prepare_app = WebServer.prepare_app
        _publish_app = WebServer._publish_app

        def __init__(self):
            self.app = SimpleNamespace(state=SimpleNamespace())
            self._feature_setup = DeferredSetup(build)

        async def stop(self):
            stopped.set()
            await self._feature_setup.stop()
            assert build.finished.is_set()
            events.append("resource cleanup")

    server = Server()
    monkeypatch.setattr(runtime_refs, "set_web_app", publications.append)
    monkeypatch.setattr(desktop, "META_FILE_PATH", tmp_path / "instance.json")
    offload = SimpleNamespace(target=lambda: None)
    monkeypatch.setattr(ide, "offload_on_quit", offload, raising=False)
    monkeypatch.setitem(sys.modules, "jarvis.agentic_ide.offload_on_quit", offload)
    monkeypatch.setitem(
        sys.modules, "jarvis.agentic_ide.session", SimpleNamespace(get_registry=lambda: None)
    )

    def backend():
        asyncio.set_event_loop(loop)
        loop.call_soon(loop_started.set)
        try:
            loop.run_forever()
        finally:
            loop.close()
            events.append("loop closed")
            closed.set()

    thread = threading.Thread(target=backend)
    thread.start()
    assert loop_started.wait(5)
    preparing = asyncio.run_coroutine_threadsafe(server.prepare_app(), loop)
    assert build.entered.wait(5)

    app = desktop.DesktopApp.__new__(desktop.DesktopApp)
    app._shutdown_done = False
    app._virtual_cursor = app._jarvis_cursor = app._tray = None
    app._pipeline_task = None
    app._bootstrap = None
    app._backend_loop = loop
    app._backend_thread = thread
    app._server = server
    app._BACKEND_SHUTDOWN_WAIT_S = 0.01
    monkeypatch.setattr(app, "_destroy_background_keeper", lambda: None)
    monkeypatch.setattr(app, "_stop_overlay", lambda: None)

    try:
        assert app.shutdown() == 0
        assert stopped.wait(5)
        assert thread.is_alive()
        assert loop.is_running()
        assert not closed.is_set()
        assert publications == []
        build.release.set()
        with pytest.raises(RuntimeError, match="stopped before publication"):
            preparing.result(timeout=5)
        assert closed.wait(5)
        thread.join(timeout=5)
        assert events == ["resource cleanup", "loop closed"]
        assert publications == []
        assert not thread.is_alive()
    finally:
        build.release.set()
        if not closed.wait(5):
            loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)


def test_process_exit_waits_off_gui_thread_for_backend_cleanup(monkeypatch) -> None:
    from concurrent.futures import Future

    from jarvis.ui import desktop_app as desktop

    future = Future()
    exited = threading.Event()
    codes = []
    app = desktop.DesktopApp.__new__(desktop.DesktopApp)
    app._backend_shutdown_future = future
    app._backend_thread = None

    def exit_process(code):
        codes.append(code)
        exited.set()

    monkeypatch.setattr(desktop.os, "_exit", exit_process)
    app._exit_after_backend_shutdown(0)
    try:
        assert not exited.is_set()
        assert app._shutdown_exit_thread.is_alive()
        assert app._shutdown_exit_thread.daemon is False
        future.set_result(None)
        assert exited.wait(5)
        assert codes == [0]
    finally:
        if not future.done():
            future.set_result(None)
        app._shutdown_exit_thread.join(timeout=5)
