"""Plain shell tabs use their named workspace and clean up their own PTY."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.agentic_ide import session
from jarvis.ui.web import workspace_routes as routes
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def shell_app(monkeypatch, tmp_path):
    workspace = SimpleNamespace(folder=tmp_path)
    registry = SimpleNamespace(get=lambda key: workspace if key == "workspace-a" else None)
    monkeypatch.setattr(session, "get_registry", lambda: registry)
    monkeypatch.setattr(routes, "credentials_valid", lambda scope: True)
    monkeypatch.setattr(routes, "build_agent_argv", lambda name: ("/bin/test-shell",))
    app = FastAPI()
    app.include_router(routes.router)
    manager = FakePtyManager()
    app.state.workspace_pty = manager
    return TestClient(app), manager, tmp_path


def test_shell_tabs_are_independent_and_use_the_named_workspace(shell_app):
    client, manager, folder = shell_app
    with client.websocket_connect(
        "/api/workspace/pty/one?agent=shell&workspace_id=workspace-a"
    ) as first:
        assert first.receive_json() == {"t": "ready"}
        with client.websocket_connect(
            "/api/workspace/pty/two?agent=shell&workspace_id=workspace-a"
        ) as second:
            assert second.receive_json() == {"t": "ready"}
            first.send_json({"t": "i", "d": "pwd\r"})
            first.send_json({"t": "r", "cols": 92, "rows": 30})
        assert manager.closed == ["fake-pty-2"]
    assert manager.closed == ["fake-pty-2", "fake-pty-1"]
    assert manager.writes == [("fake-pty-1", "pwd\r")]
    assert manager.resizes == [("fake-pty-1", 92, 30)]
    assert len(manager.spawns) == 2
    assert all(spawn["cwd"] == str(folder) for spawn in manager.spawns)
    assert all(spawn["argv"] == ("/bin/test-shell",) for spawn in manager.spawns)


@pytest.mark.parametrize("query", ["", "&workspace_id=unknown"])
def test_shell_requires_a_known_workspace_without_falling_back(shell_app, query):
    client, manager, _ = shell_app
    with client.websocket_connect(f"/api/workspace/pty/one?agent=shell{query}") as socket:
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4404
    assert manager.spawns == []


def test_shell_preserves_authorization(shell_app, monkeypatch):
    client, manager, _ = shell_app
    monkeypatch.setattr(routes, "credentials_valid", lambda scope: False)
    with client.websocket_connect(
        "/api/workspace/pty/one?agent=shell&workspace_id=workspace-a"
    ) as socket:
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4401
    assert manager.spawns == []


def test_malformed_receive_closes_the_shell_instead_of_looping(shell_app):
    client, manager, _ = shell_app
    with client.websocket_connect(
        "/api/workspace/pty/one?agent=shell&workspace_id=workspace-a"
    ) as socket:
        assert socket.receive_json() == {"t": "ready"}
        socket.send_text("invalid JSON")
    assert manager.closed == ["fake-pty-1"]
