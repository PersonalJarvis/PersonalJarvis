"""Independent server ownership, credential-free endpoints and atomic settings."""

from __future__ import annotations

import asyncio
import tomllib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core import background_service as bg
from jarvis.core.config import BackgroundConfig
from jarvis.core.server_endpoint import server_endpoint


@pytest.mark.parametrize("url", [
    "https://jarvis.example.com", "http://127.0.0.1:47821", "http://[::1]:47821/",
])
def test_valid_endpoint(url):
    assert server_endpoint(url) == url.rstrip("/")


@pytest.mark.parametrize("url", [
    "http://jarvis.example.com", "https://user:secret@example.com", "https://example.com?key=x",
    "file:///etc/passwd", "https://example.com/#secret", "https://example.com/api",
    "http://localhost:0", "http://localhost:99999", "//example.com",
])
def test_endpoint_rejects_insecure_or_credential_bearing_address(url):
    with pytest.raises(ValueError):
        server_endpoint(url)


def test_setting_is_atomic_and_preserves_the_other_background_options(tmp_path):
    from jarvis.core.config_writer import set_agent_server

    path = tmp_path / "jarvis.toml"
    path.write_text("[background]\nkeep_agents_running = false\n", encoding="utf8")
    set_agent_server(persistent=True, server_url="https://jarvis.example.com/", path=path)
    config = BackgroundConfig(**tomllib.loads(path.read_text(encoding="utf8"))["background"])
    assert config.persistent_server
    assert config.server_url == "https://jarvis.example.com"
    assert not config.keep_agents_running
    before = path.read_bytes()
    with pytest.raises(ValueError):
        set_agent_server(persistent=False, server_url="http://public.example.com", path=path)
    assert path.read_bytes() == before


def test_server_spawn_keeps_independent_mode_and_instance(monkeypatch):
    from jarvis.core.instance import resolve_instance

    monkeypatch.setattr(
        "jarvis.core.instance.current_instance",
        lambda: resolve_instance({"JARVIS_INSTANCE": "dev"}),
    )
    command = bg.service_command(after_pid=None, persistent=True, frozen=False, executable="python")
    assert "--persistent-server" in command
    assert command[command.index("--instance") + 1] == "dev"
    assert "--after-pid" not in command


async def test_persistent_server_has_no_idle_or_handover_watch(monkeypatch):
    from jarvis.ui import background_tray
    from jarvis.ui.web import launcher

    class Tray:
        def __init__(self, **kwargs):
            self.stop_owner = kwargs["on_stop"]

        def start(self):
            return True

    monkeypatch.setattr(background_tray, "BackgroundTray", Tray)
    stop = asyncio.Event()
    task, tray = launcher._start_background_service_duties(
        SimpleNamespace(), stop, persistent=True,
    )
    assert task is None
    assert not stop.is_set()
    tray.stop_owner()
    await asyncio.sleep(0)
    assert stop.is_set()


def test_reopening_never_takes_over_a_persistent_server(monkeypatch):
    from jarvis.ui.web import launcher

    monkeypatch.setattr(bg, "read_marker", lambda: {"persistent": True})

    def forbidden(*args, **kwargs):
        pytest.fail("A client must not request a server handover")

    monkeypatch.setattr(bg, "take_over_from_service", forbidden)
    assert launcher._take_over_from_background_service() is None


def test_explicit_stop_is_separate_from_client_connections():
    from jarvis.ui.web.agent_server_routes import router

    app = FastAPI()
    app.include_router(router)
    stops = []
    app.state.agent_server_mode = True
    app.state.agent_server_ready = True
    app.state.agent_server_stop = lambda: stops.append("stopped")
    for _ in range(2):
        with TestClient(app) as client:
            assert client.get("/api/agent-server/status").json()["ready"]
    assert stops == []
    with TestClient(app) as client:
        assert client.post("/api/agent-server/stop").json() == {"stopping": True}
    assert stops == ["stopped"]
    assert app.openapi()["paths"]["/api/agent-server/stop"]["post"]["x-jarvis-dangerous"]


async def test_agent_services_start_without_a_client(monkeypatch):
    from jarvis.ui.web import agent_chat_routes
    from jarvis.ui.web.agent_server import start_agents

    restored = []

    class Runtime:
        async def ensure_started(self):
            restored.append("society")

    service = object()
    monkeypatch.setattr(agent_chat_routes, "_service_from_state", lambda state: service)
    runtime = Runtime()
    state = SimpleNamespace(society=None, society_factory=lambda: runtime)
    await start_agents(state)
    assert state.agent_server_ready and state.society is runtime
    assert restored == ["society"]


def test_concurrent_clients_launch_only_one_server(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from jarvis.ui import desktop_app, server_client

    marker = {}
    starts = []
    monkeypatch.setattr(bg, "marker_path", lambda: tmp_path / "service.json")
    monkeypatch.setattr(bg, "read_marker", lambda: marker)
    monkeypatch.setattr(bg, "service_pid", lambda: 42 if marker else None)
    monkeypatch.setattr(desktop_app, "_read_meta", lambda: {})
    monkeypatch.setattr(server_client, "wait_for_server", lambda url: None)

    def spawn(**kwargs):
        starts.append(kwargs)
        marker.update(persistent=True, port=47999)
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(server_client.local_server, port=47999, spawn=spawn) for _ in range(2)]
        assert [job.result() for job in jobs] == ["http://127.0.0.1:47999"] * 2
    assert starts == [{"after_pid": None, "persistent": True, "port": 47999}]


def test_local_client_refuses_to_interrupt_integrated_owner(tmp_path, monkeypatch):
    from jarvis.ui import desktop_app, server_client

    monkeypatch.setattr(bg, "marker_path", lambda: tmp_path / "service.json")
    monkeypatch.setattr(bg, "read_marker", lambda: None)
    monkeypatch.setattr(bg, "service_pid", lambda: None)
    monkeypatch.setattr(desktop_app, "_read_meta", lambda: {"pid": 42})
    monkeypatch.setattr(desktop_app, "_pid_alive", lambda pid: True)
    with pytest.raises(RuntimeError, match="integrated desktop"):
        server_client.local_server(port=47999, spawn=lambda **kw: pytest.fail("must not spawn"))


def test_remote_readiness_never_reads_or_sends_local_credentials(monkeypatch):
    from jarvis.core import control_key
    from jarvis.ui.server_client import wait_for_server

    monkeypatch.setattr(control_key, "get_control_key", lambda: pytest.fail("must not read key"))
    with pytest.raises(ValueError, match="loopback"):
        wait_for_server("https://jarvis.example.com", timeout_s=0.1)


def test_direct_persistent_entry_is_recognized(monkeypatch):
    import psutil

    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(
        cmdline=lambda: ["python", "-m", "jarvis", "--persistent-server"],
    ))
    assert bg._is_service_process(42)


def test_enabling_server_mode_hands_off_even_without_scheduled_work(monkeypatch):
    started = []
    monkeypatch.setattr(bg, "clear_handover_request", lambda: None)
    monkeypatch.setattr(bg, "spawn_service", lambda **kw: started.append(kw) or True)
    cfg = SimpleNamespace(background=BackgroundConfig(persistent_server=True))
    assert bg.hand_off_on_quit(SimpleNamespace(), cfg, pid=42)
    assert started == [{"after_pid": 42, "persistent": True}]
