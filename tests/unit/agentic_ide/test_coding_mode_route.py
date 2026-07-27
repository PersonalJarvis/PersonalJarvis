"""Coding mode has to be announced, not just stored.

The app-wide indicator lives in the shell, which never mounts the workspace
view. So every route that can change the answer to "is Jarvis an Agentic IDE
right now?" must put it on the bus — otherwise the badge is only correct on the
one screen where the user can already see the terminals, which is the screen it
was not built for.

Each test here pins one transition that used to be silent.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry, reset_registry
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def clean_registry():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    reg = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "get_registry", lambda: reg)
    from jarvis.ui.web import agentic_ide_routes

    monkeypatch.setattr(agentic_ide_routes, "get_registry", lambda: reg)
    return reg


class _RecordingBus:
    """Collects the coding-mode announcements a real client would receive."""

    def __init__(self) -> None:
        self.modes: list[dict] = []

    async def publish(self, event: object) -> None:
        if type(event).__name__ != "AgenticIdeCodingModeChanged":
            return
        self.modes.append(
            {
                "enabled": event.enabled,
                "session_id": event.session_id,
                "workspace": event.workspace,
            }
        )


@pytest.fixture
def bus() -> _RecordingBus:
    return _RecordingBus()


@pytest.fixture
def client(registry: Registry, bus: _RecordingBus) -> TestClient:
    from jarvis.ui.web.agentic_ide_routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.bus = bus
    return TestClient(app)


async def _workspace(registry: Registry, folder: Path) -> None:
    await registry.start(str(folder), [{"agent": "claude"}])


async def test_toggling_the_mode_announces_it(
    client: TestClient, registry: Registry, bus: _RecordingBus, tmp_path: Path
) -> None:
    await _workspace(registry, tmp_path)

    assert client.put("/api/agentic-ide/mode", json={"enabled": True}).status_code == 200

    assert [m["enabled"] for m in bus.modes] == [True]
    assert bus.modes[0]["workspace"]


async def test_leaving_the_mode_announces_it_too(
    client: TestClient, registry: Registry, bus: _RecordingBus, tmp_path: Path
) -> None:
    """A badge that only ever hears 'on' is worse than none."""
    await _workspace(registry, tmp_path)
    client.put("/api/agentic-ide/mode", json={"enabled": True})
    bus.modes.clear()

    client.put("/api/agentic-ide/mode", json={"enabled": False})

    assert [m["enabled"] for m in bus.modes] == [False]


async def test_closing_the_workspace_announces_the_mode_ending(
    client: TestClient, registry: Registry, bus: _RecordingBus, tmp_path: Path
) -> None:
    """Nobody touched the toggle, yet the mode is over — the badge must learn."""
    await _workspace(registry, tmp_path)
    client.put("/api/agentic-ide/mode", json={"enabled": True})
    bus.modes.clear()

    assert client.delete("/api/agentic-ide/session").status_code == 200

    assert bus.modes and bus.modes[-1]["enabled"] is False


async def test_switching_workspaces_announces_the_new_mode(
    client: TestClient, registry: Registry, bus: _RecordingBus, tmp_path: Path
) -> None:
    """Each workspace carries its own mode, so the front one decides the badge."""
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    coding = await registry.start(str(first), [{"agent": "claude"}])
    plain = await registry.start(str(second), [{"agent": "claude"}])
    client.put("/api/agentic-ide/workspaces/active", json={"id": coding.id})
    client.put("/api/agentic-ide/mode", json={"enabled": True})
    bus.modes.clear()

    # Bring the workspace that is NOT in coding mode to the front.
    client.put("/api/agentic-ide/workspaces/active", json={"id": plain.id})

    assert bus.modes and bus.modes[-1]["enabled"] is False


async def test_closing_one_workspace_by_id_announces_the_mode(
    client: TestClient, registry: Registry, bus: _RecordingBus, tmp_path: Path
) -> None:
    await _workspace(registry, tmp_path)
    assert registry.session is not None
    workspace_id = registry.session.id
    client.put("/api/agentic-ide/mode", json={"enabled": True})
    bus.modes.clear()

    client.delete(f"/api/agentic-ide/workspaces/{workspace_id}")

    assert bus.modes and bus.modes[-1]["enabled"] is False


async def test_a_missing_bus_never_breaks_the_toggle(
    registry: Registry, tmp_path: Path
) -> None:
    """Headless hosts and tests have no bus; the mode must still switch."""
    from jarvis.ui.web.agentic_ide_routes import router

    app = FastAPI()
    app.include_router(router)
    bus_less = TestClient(app)
    await _workspace(registry, tmp_path)

    response = bus_less.put("/api/agentic-ide/mode", json={"enabled": True})

    assert response.status_code == 200
    assert response.json()["focus_mode"] is True
