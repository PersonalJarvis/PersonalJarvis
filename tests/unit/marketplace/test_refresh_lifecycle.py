"""Shared WebServer lifecycle coverage for marketplace OAuth refresh."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


class _FakeRefreshScheduler:
    instances: list[_FakeRefreshScheduler] = []

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.args = args
        self.kwargs = kwargs
        self.start_calls = 0
        self.stop_calls = 0
        self.instances.append(self)

    def start(self) -> None:
        self.start_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1


@pytest.fixture(autouse=True)
def _reset_fake_scheduler() -> None:
    _FakeRefreshScheduler.instances.clear()


@pytest.mark.asyncio
async def test_refresh_scheduler_is_deferred_and_started_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.marketplace import connect_helpers, refresh_scheduler

    monkeypatch.setattr(refresh_scheduler, "RefreshScheduler", _FakeRefreshScheduler)
    monkeypatch.setattr(connect_helpers, "connected_plugin_ids", lambda _store: [])
    monkeypatch.setattr(connect_helpers, "build_handler_from_catalog", lambda _pid: None)

    server = WebServer(JarvisConfig(), bus=EventBus())
    server._schedule_marketplace_refresh_scheduler()
    server._schedule_marketplace_refresh_scheduler()

    # call_soon keeps catalog/keyring work outside the caller's readiness turn.
    assert _FakeRefreshScheduler.instances == []
    await asyncio.sleep(0)

    assert len(_FakeRefreshScheduler.instances) == 1
    scheduler = _FakeRefreshScheduler.instances[0]
    assert scheduler.start_calls == 1
    assert server.app.state.refresh_scheduler is scheduler

    server._schedule_marketplace_refresh_scheduler()
    await asyncio.sleep(0)
    assert len(_FakeRefreshScheduler.instances) == 1

    await server.stop()
    assert scheduler.stop_calls == 1
    assert server.app.state.refresh_scheduler is None


@pytest.mark.asyncio
async def test_stop_cancels_a_deferred_scheduler_start() -> None:
    server = WebServer(JarvisConfig(), bus=EventBus())
    server._schedule_marketplace_refresh_scheduler()

    await server._stop_marketplace_refresh_scheduler()
    await asyncio.sleep(0)

    assert server._refresh_scheduler is None
    assert server._refresh_scheduler_start_handle is None
    assert server.app.state.refresh_scheduler is None


@pytest.mark.asyncio
async def test_stop_cancels_queued_live_session_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.marketplace import connect_helpers, refresh_scheduler

    monkeypatch.setattr(refresh_scheduler, "RefreshScheduler", _FakeRefreshScheduler)
    monkeypatch.setattr(connect_helpers, "connected_plugin_ids", lambda _store: [])
    monkeypatch.setattr(connect_helpers, "build_handler_from_catalog", lambda _pid: None)

    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class _BlockingRegistry:
        calls = 0

        async def refresh_plugin(self, _plugin_id: str) -> None:
            self.calls += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    server = WebServer(JarvisConfig(), bus=EventBus())
    registry = _BlockingRegistry()
    server._plugin_registry = registry
    server._schedule_marketplace_refresh_scheduler()
    await asyncio.sleep(0)

    scheduler = _FakeRefreshScheduler.instances[0]
    scheduler.kwargs["on_refreshed"]("gmail")
    await entered.wait()
    assert len(server._refresh_registry_tasks) == 1

    await server._stop_marketplace_refresh_scheduler()

    assert cancelled.is_set()
    assert server._refresh_registry_tasks == set()
    scheduler.kwargs["on_refreshed"]("google_calendar")
    await asyncio.sleep(0)
    assert registry.calls == 1


@pytest.mark.asyncio
async def test_shared_webserver_start_schedules_refresh_after_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = WebServer(JarvisConfig(), bus=EventBus())
    scheduled: list[str] = []

    async def _noop_async() -> None:
        return None

    for name in (
        "_init_mission_stack",
        "_init_screenshot_retention",
        "_init_flight_recorder",
        "_init_wiki_integration",
        "_init_task_stack",
        "_init_channel_stack",
    ):
        monkeypatch.setattr(server, name, _noop_async)
    monkeypatch.setattr(server, "_init_wiki_boot_index", lambda **_kwargs: None)
    monkeypatch.setattr(server, "_init_wiki_watcher", lambda: None)
    monkeypatch.setattr(server, "_init_session_stack", lambda: None)
    monkeypatch.setattr(
        server,
        "_schedule_marketplace_refresh_scheduler",
        lambda: scheduled.append("refresh"),
    )
    server._voice_ready = True
    server._skill_registry = None
    server._doc_registry = None
    server._cli_registry = None
    server._plugin_registry = None
    server._board_aggregator = None
    server._board_evaluator = None
    server._bio_scheduler = None
    server._pending_reloads.clear()

    await server.start(start_serving=False)
    await server._channel_stack_task

    assert scheduled == ["refresh"]
