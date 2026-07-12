from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jarvis.ui.desktop_app import DesktopApp


class _FakeBus:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[type[Any], Any]] = []

    def subscribe(self, event_type: type[Any], handler: Any) -> None:
        self.subscriptions.append((event_type, handler))


class _FakeApp:
    def __init__(self) -> None:
        self.state = SimpleNamespace()

        class _Router:
            routes: list[Any] = []

        self.router = _Router()

    def post(self, *_args: Any, **_kwargs: Any) -> Any:
        def _decorator(func: Any) -> Any:
            return func

        return _decorator


class _FakeWebServer:
    # The test sets this to the shared events list so ``start`` records when the
    # heavy ``server.start()`` _init_* chain runs relative to the wake listener.
    events: list[str] | None = None

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.bus = _FakeBus()
        self.app = _FakeApp()
        self.stopped = False

    async def start(self, *, start_serving: bool = True) -> None:
        if _FakeWebServer.events is not None:
            _FakeWebServer.events.append("server_start")
        self.start_serving = start_serving
        return None

    async def stop(self) -> None:
        self.stopped = True


class _FakeBootstrap:
    """Stand-in for FastBootstrap: no real socket bind in the unit test."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.app_set: Any = None

    async def serve(self, _host: str, _port: int) -> None:
        return None

    def set_app(self, app: Any) -> None:
        self.app_set = app

    async def wait_shell_painted(self, timeout: float = 0.0) -> bool:  # noqa: ASYNC109
        return True

    async def stop(self) -> None:
        return None


class _FakeChatStore:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def open(self) -> None:
        pass

    def prune_older_than(self, _days: int) -> None:
        pass

    async def add_message(self, *_args: Any, **_kwargs: Any) -> None:
        pass


class _FakeSupervisor:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def set_state(self, _state: str) -> None:
        pass


class _FakeMCPRegistry:
    def load_from_mcp_json(self) -> None:
        pass

    async def start_enabled(self, _enabled: list[str]) -> None:
        pass


async def _async_zero() -> int:
    return 0


class _FakeLoop:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.callbacks: list[tuple[Any, tuple[Any, ...]]] = []
        self.tasks: list[Any] = []

    def set_exception_handler(self, _handler: Any) -> None:
        pass

    def run_until_complete(self, coro: Any) -> Any:
        return asyncio.run(coro)

    def create_task(self, coro: Any, **_kwargs: Any) -> SimpleNamespace:
        self.tasks.append(coro)
        return SimpleNamespace(
            cancel=lambda: None,
            done=lambda: True,
            add_done_callback=lambda _cb: None,
        )

    def call_soon(self, callback: Any, *args: Any) -> None:
        self.callbacks.append((callback, args))

    def call_later(self, _delay: float, callback: Any, *args: Any) -> None:
        self.callbacks.append((callback, args))

    def run_forever(self) -> None:
        self.events.append("run_forever")
        while self.tasks or self.callbacks:
            pending_tasks = list(self.tasks)
            self.tasks.clear()
            for coro in pending_tasks:
                asyncio.run(coro)
            pending_callbacks = list(self.callbacks)
            self.callbacks.clear()
            for callback, args in pending_callbacks:
                callback(*args)


def _install_fake_module(monkeypatch, name: str, **attrs: Any) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _cfg(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        memory=SimpleNamespace(data_dir=tmp_path),
        latency=SimpleNamespace(log_jsonl=False),
        harness=SimpleNamespace(default_risk_tier="safe"),
        ui=SimpleNamespace(admin_api_port=18123),
    )


def test_desktop_voice_start_does_not_wait_for_brain_ready(monkeypatch, tmp_path):
    events: list[str] = []
    monkeypatch.setattr(asyncio, "new_event_loop", lambda: _FakeLoop(events))
    monkeypatch.setattr(asyncio, "set_event_loop", lambda _loop: None)

    async def _to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    _install_fake_module(
        monkeypatch,
        "jarvis.speech.warmup_prefetch",
        start_wake_import_prefetch=lambda: None,
        start_tts_import_prefetch=lambda: None,
    )
    _install_fake_module(
        monkeypatch,
        "jarvis.audio.device_init",
        start_audio_device_prefetch=lambda: None,
    )
    _install_fake_module(
        monkeypatch,
        "jarvis.ui.web.server",
        WebServer=_FakeWebServer,
    )
    _install_fake_module(
        monkeypatch,
        "jarvis.ui.web.fast_bootstrap",
        FastBootstrap=_FakeBootstrap,
    )
    _install_fake_module(
        monkeypatch,
        "jarvis.state.chat_store",
        ChatStore=_FakeChatStore,
        default_chats_db_path=lambda _data_dir: tmp_path / "chats.db",
    )
    _install_fake_module(
        monkeypatch,
        "jarvis.state.supervisor",
        Supervisor=_FakeSupervisor,
    )
    _install_fake_module(
        monkeypatch,
        "jarvis.brain.factory",
        build_default_brain=lambda **_kwargs: (
            events.append("build_brain") or SimpleNamespace(active_provider="fake")
        ),
    )

    async def _no_switches(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    _install_fake_module(
        monkeypatch,
        "jarvis.brain.frontier_autoswitch",
        apply_frontier_resolution=_no_switches,
    )
    _install_fake_module(
        monkeypatch,
        "jarvis.brain.frontier_resolver",
        FrontierResolver=lambda **_kwargs: object(),
    )
    fake_mcp_state = _install_fake_module(
        monkeypatch,
        "jarvis.mcp.state",
        get_enabled_names=lambda: [],
    )
    fake_mcp_pkg = types.ModuleType("jarvis.mcp")
    fake_mcp_pkg.state = fake_mcp_state
    fake_mcp_pkg.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "jarvis.mcp", fake_mcp_pkg)
    _install_fake_module(
        monkeypatch,
        "jarvis.mcp.registry",
        MCPRegistry=_FakeMCPRegistry,
    )
    _install_fake_module(
        monkeypatch,
        "jarvis.core.runtime_refs",
        set_mcp_registry=lambda _registry: None,
    )

    class _FakeWorkflowStore:
        pass

    _install_fake_module(
        monkeypatch,
        "jarvis.workflows",
        WorkflowRunner=lambda **_kwargs: object(),
        WorkflowScheduler=lambda **_kwargs: object(),
        WorkflowStore=lambda _path: _FakeWorkflowStore(),
        ensure_seed_workflows=lambda _store: _async_zero(),
    )
    _install_fake_module(
        monkeypatch,
        "conductor",
        ConductorStore=lambda: object(),
        Runner=lambda _store: object(),
        Scheduler=lambda _store, _runner: object(),
        ensure_seed_jobs=lambda _store: _async_zero(),
    )

    app = DesktopApp.__new__(DesktopApp)
    app.cfg = _cfg(tmp_path)
    app._backend_loop = None
    app._server = None
    app._workflow_store = None
    app._workflow_scheduler = None
    app._conductor_store = None
    app._conductor_scheduler = None

    async def _speech(*_args: Any, **_kwargs: Any) -> None:
        events.append("speech")
        # The real _start_speech_and_orb signals this once the wake model has
        # loaded; the heavy backend gates the brain/mcp build on it (GIL
        # priority for the wake-model load). Set it so the gate releases.
        app._wake_model_loaded.set()

    def _cursor() -> None:
        events.append("cursor")

    monkeypatch.setattr(app, "_start_speech_and_orb", _speech)
    monkeypatch.setattr(app, "_start_virtual_cursor", _cursor)
    _FakeWebServer.events = events
    try:
        app._run_backend()
    finally:
        _FakeWebServer.events = None

    # Serve-WAKE-first contract: the Jarvis-Bar / wake listener must arm as soon
    # as the backend loop is serving — BEFORE the heavy ``server.start()`` _init_*
    # chain (mission/wiki/session/channel) AND before the BrainManager build. The
    # window appears, then the wake word, then the rest, all overlapping. A slow
    # backend must never keep the wake word deaf after the window is visible.
    assert events[0] == "run_forever"
    assert events[1] == "speech"
    assert "server_start" in events
    assert events.index("speech") < events.index("server_start"), (
        f"wake must arm before the heavy server.start() chain; got {events}"
    )
    assert events.index("speech") < events.index("build_brain"), (
        f"wake must arm before the brain build; got {events}"
    )
