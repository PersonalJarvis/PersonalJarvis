"""Shell acknowledgement and shared startup do not provision optional browsers."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


def test_full_app_accepts_shell_painted_acknowledgement(monkeypatch) -> None:
    from jarvis.society.browser import install

    installs = []
    monkeypatch.setattr(install, "start_install", lambda *args: installs.append(args))
    server = WebServer(JarvisConfig(), bus=EventBus())
    origin = "http://localhost:47821"

    with TestClient(server.app, base_url=origin) as client:
        response = client.post(
            "/api/ui/shell-painted",
            headers={"Origin": origin},
        )

    assert response.status_code == 204
    assert response.content == b""
    assert installs == []


@pytest.mark.asyncio
async def test_shared_startup_does_not_provision_optional_browser(monkeypatch, tmp_path):
    from jarvis.memory.learning import loop as learning
    from jarvis.society.browser import install
    from jarvis.ui.web import mars_routes

    installs = []
    monkeypatch.setattr(install, "start_install", lambda *args: installs.append(args))
    monkeypatch.setattr(learning, "start_learning", lambda *args: None)
    monkeypatch.setattr(mars_routes, "schedule_mars_resume", lambda *args: None)
    cfg = JarvisConfig()
    cfg.memory.data_dir = str(tmp_path)
    server = WebServer(cfg, bus=EventBus())

    async def noop_async():
        return None

    for name in (
        "_init_mission_stack", "_init_screenshot_retention", "_init_flight_recorder",
        "_init_wiki_integration", "_init_task_stack", "_init_channel_stack",
    ):
        monkeypatch.setattr(server, name, noop_async)
    for name in (
        "_init_wiki_boot_index", "_init_wiki_watcher", "_init_session_stack",
        "_schedule_anyio_pool_warm", "_schedule_registry_bootstraps",
        "_schedule_marketplace_refresh_scheduler", "_start_local_models_health_monitor",
        "_schedule_local_models_autostart", "_schedule_realtime_transport_warm",
        "_schedule_appshot_shortcut",
    ):
        monkeypatch.setattr(server, name, lambda **kwargs: None)
    for name in (
        "_skill_registry", "_doc_registry", "_cli_registry", "_plugin_registry",
        "_board_aggregator", "_board_evaluator", "_bio_scheduler",
    ):
        setattr(server, name, None)
    server._voice_ready = True
    server._pending_reloads.clear()

    await server.start(start_serving=False)
    await server._channel_stack_task
    await asyncio.sleep(0)

    assert installs == []
