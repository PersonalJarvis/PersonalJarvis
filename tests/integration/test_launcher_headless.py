"""Integration-Test für `_run_headless` mit Mock-WebServer.

Simuliert: Launcher startet, wird gecancelt → sauberer Shutdown.

Alle schweren Abhängigkeiten (uvicorn, WebServer, ChatStore, Supervisor,
MCPRegistry, load_config, …) werden durch leichte Stubs ersetzt, damit
der Test rein den START/STOP-Lebenszyklus von _run_headless prüft und
weder echten Port bindet noch auf laufende Dienste angewiesen ist.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Lightweight fakes shared by all stubs below
# ---------------------------------------------------------------------------


class _FakeBus:
    """Minimal event-bus — subscribe / publish sind keine-ops."""

    def subscribe(self, *args: object, **kwargs: object) -> None:
        pass

    async def publish(self, event: object) -> None:
        pass


class _FakeState:
    """Nimmt beliebige Attribut-Zuweisung (mirrors Starlette State)."""


class _FakeApp:
    """Minimales ASGI-App-Objekt mit veränderbarem state-Bag."""

    def __init__(self) -> None:
        self.state = _FakeState()

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        pass


# ---------------------------------------------------------------------------
# Mock WebServer — zentrales Lifecycle-Testsubjekt
# ---------------------------------------------------------------------------


class _MockWebServer:
    """Stand-in für jarvis.ui.web.server.WebServer."""

    instances: list["_MockWebServer"] = []

    def __init__(self, cfg):
        self.cfg = cfg
        self.started = False
        self.stopped = False
        # _run_headless uses server.bus to subscribe/publish events.
        self.bus = _FakeBus()
        # _run_headless sets server.app.state.* before calling server.start().
        self.app = _FakeApp()
        _MockWebServer.instances.append(self)

    async def start(self, *, start_serving: bool = True) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


# ---------------------------------------------------------------------------
# Fake peripheral subsystems
# ---------------------------------------------------------------------------


class _MockChatStore:
    def __init__(self, *, bus, db_path):  # noqa: ANN001
        pass

    def open(self) -> None:
        pass

    def prune_older_than(self, days: int) -> None:
        pass

    async def add_message(self, **kwargs: object) -> None:
        pass


class _MockSupervisor:
    def __init__(self, *, bus):  # noqa: ANN001
        pass


class _MockMCPRegistry:
    def load_from_mcp_json(self) -> None:
        pass

    async def start_enabled(self, names: list) -> None:  # noqa: ANN001
        pass


# ---------------------------------------------------------------------------
# Fake uvicorn Server — avoids real TCP binding
# ---------------------------------------------------------------------------


class _FakeUvicornServer:
    """Sets started=True, exits loop when should_exit is set."""

    def __init__(self, config: object) -> None:
        self.config = config
        self.started = False
        self.should_exit = False

    async def serve(self) -> None:
        self.started = True
        while not self.should_exit:
            await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Fake config — replaces load_config() return value
# ---------------------------------------------------------------------------


def _make_fake_loaded_config(data_dir: str = "/tmp", port: int = 18123) -> SimpleNamespace:
    """Minimal config satisfying all attribute accesses inside _run_headless."""
    ui = SimpleNamespace(admin_api_port=port, dev_mode=False)
    # _run_headless calls cfg.model_copy(update={...}) when args.port is not None.
    ui.model_copy = lambda *, update=None: ui  # type: ignore[assignment]
    cfg = SimpleNamespace(
        ui=ui,
        memory=SimpleNamespace(data_dir=data_dir),
        marketplace=SimpleNamespace(public_callback_base_url=""),
        harness=SimpleNamespace(default_risk_tier="ask"),
    )
    cfg.model_copy = lambda *, update=None: cfg  # type: ignore[assignment]
    return cfg


# ---------------------------------------------------------------------------
# Central autouse fixture: patches every heavyweight dependency
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_headless_deps(monkeypatch, tmp_path):  # noqa: ANN001
    """Replace all subsystems that _run_headless imports locally.

    Strategy: always import the REAL module first so its internal import
    chain (e.g. jarvis.mcp.__init__ → adapter → client → registry) can
    satisfy its own `from .registry import MCPServerSpec` etc.  Then patch
    only the specific class / function the _run_headless lifecycle needs.

    This avoids the cascading ImportError that occurs when sys.modules
    substitution breaks an already-partially-imported package.
    """
    _MockWebServer.instances.clear()

    # 1. uvicorn — import real module, replace Server/Config
    import uvicorn  # noqa: PLC0415

    class _FakeUvicornConfig:
        def __init__(self, **kw: object) -> None:
            self.port = kw.get("port", 0)

    monkeypatch.setattr(uvicorn, "Server", _FakeUvicornServer)
    monkeypatch.setattr(uvicorn, "Config", _FakeUvicornConfig)

    # 2. jarvis.core.config — patch load_config and helpers
    import jarvis.core.config as _jcfg  # noqa: PLC0415

    _fake_cfg = _make_fake_loaded_config(data_dir=str(tmp_path))
    monkeypatch.setattr(_jcfg, "load_config", lambda: _fake_cfg)
    monkeypatch.setattr(_jcfg, "ensure_project_root_cwd", lambda: None)
    monkeypatch.setattr(
        _jcfg, "refresh_persisted_env_from_user_registry", lambda: None
    )

    # 3. WebServer — replace the class on the real module
    import jarvis.ui.web.server as _srv_mod  # noqa: PLC0415

    monkeypatch.setattr(_srv_mod, "WebServer", _MockWebServer)

    # 4. ChatStore + helper
    import jarvis.state.chat_store as _cs_mod  # noqa: PLC0415

    monkeypatch.setattr(_cs_mod, "ChatStore", _MockChatStore)
    monkeypatch.setattr(
        _cs_mod,
        "default_chats_db_path",
        lambda data_dir: str(tmp_path / "chats.db"),
    )

    # 5. Supervisor
    import jarvis.state.supervisor as _sup_mod  # noqa: PLC0415

    monkeypatch.setattr(_sup_mod, "Supervisor", _MockSupervisor)

    # 6. Brain factory (invoked in a background task — needs to be a no-op)
    import jarvis.brain.factory as _brain_mod  # noqa: PLC0415

    monkeypatch.setattr(_brain_mod, "build_default_brain", lambda **kw: None)

    # 7. MCP registry — patch MCPRegistry on the real module so internal
    #    imports (jarvis.mcp.__init__ → adapter → client → registry) still
    #    resolve MCPServerSpec from the real module.
    import jarvis.mcp.registry as _mcp_reg_mod  # noqa: PLC0415

    monkeypatch.setattr(_mcp_reg_mod, "MCPRegistry", _MockMCPRegistry)

    # 8. MCP state (get_enabled_names)
    import jarvis.mcp.state as _mcp_state_mod  # noqa: PLC0415

    monkeypatch.setattr(_mcp_state_mod, "get_enabled_names", lambda: [])

    # 9. runtime_refs
    import jarvis.core.runtime_refs as _rr  # noqa: PLC0415

    monkeypatch.setattr(_rr, "set_mcp_registry", lambda *a, **k: None)

    # 10. marketplace hosted_callback
    import jarvis.marketplace.hosted_callback as _hc_mod  # noqa: PLC0415

    monkeypatch.setattr(
        _hc_mod, "set_public_callback_base_url", lambda *a, **k: None
    )

    yield


# ---------------------------------------------------------------------------
# Helper: minimal args namespace
# ---------------------------------------------------------------------------


def _make_args(port: int = 18123) -> SimpleNamespace:
    # Explicit `port` prevents _fast_admin_port() from reading jarvis.toml
    # and returning the live Jarvis admin port, which would cause the fake
    # uvicorn (or a real one) to try binding an already-occupied socket.
    return SimpleNamespace(
        ui=SimpleNamespace(admin_api_port=port, dev_mode=False),
        port=port,
        dev=False,
        no_lock=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_headless_starts_and_stops_on_signal() -> None:
    """_run_headless durchläuft start → wait → cancel → stop sauber."""
    from jarvis.ui.web import launcher  # noqa: PLC0415

    args = _make_args()

    async def _driver() -> None:
        task = asyncio.create_task(launcher._run_headless(args))
        # Poll until the MockWebServer.start() has been called (≤ 1 s).
        for _ in range(100):
            await asyncio.sleep(0.01)
            if _MockWebServer.instances and _MockWebServer.instances[0].started:
                break
        # Simulate SIGINT / stop signal by cancelling the task.
        # CancelledError is raised at `await stop_event.wait()`, the finally
        # block runs and calls server.stop() → inst.stopped = True.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_driver())

    assert len(_MockWebServer.instances) == 1, "WebServer must be instantiated exactly once"
    inst = _MockWebServer.instances[0]
    assert inst.started is True, "server.start() must have been called"
    assert inst.stopped is True, "server.stop() must be called in the finally block"


def test_headless_stop_event_path() -> None:
    """Zweiter Pfad: Task wird erst nach kurzer Pause gecancelt."""
    from jarvis.ui.web import launcher  # noqa: PLC0415

    args = _make_args(port=18222)

    async def _driver() -> None:
        task = asyncio.create_task(launcher._run_headless(args))
        # Give the launcher time to reach stop_event.wait().
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_driver())

    assert _MockWebServer.instances, "WebServer should have been instantiated"
    inst = _MockWebServer.instances[-1]
    assert inst.stopped is True, "server.stop() must be called on clean shutdown"
