"""HTTP contract for creating IDE panes directly on a connected computer.

``POST /terminals`` must tell "omitted" (run where the neighbours run) from an
explicit ``null`` (this machine): collapsing the two sent a new pane of a
remote workspace to the wrong place. A server that cannot take the panes
answers 502 with the reason, and nothing is left behind.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import remote
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry
from jarvis.computers import remote_os, remote_terminal
from tests.fakes.fake_pty_manager import FakePtyManager


class RemotePool(FakePtyManager):
    """A computer's pool that answers ``run``; ``missing`` fakes absent CLIs."""

    computer_id = "c_1"

    def __init__(self) -> None:
        super().__init__()
        self.missing = ""

    async def run(self, command: str, *, timeout_s: float = 60.0) -> tuple[int, str, str]:
        return 0, self.missing, ""

    async def host(self) -> remote_os.RemoteHost:
        return remote_os.RemoteHost()


@pytest.fixture
def far(monkeypatch: pytest.MonkeyPatch) -> RemotePool:
    pool = RemotePool()
    monkeypatch.setattr(remote_terminal, "pool_for", lambda computer_id: pool)

    async def push_code(_pool: object, folder: Path) -> remote.Placement:
        return remote.Placement("/home/u/jarvis-workspaces/app-1", "f" * 40, (".env",))

    monkeypatch.setattr(remote, "push_code", push_code)
    return pool


@pytest.fixture
def client(
    far: RemotePool, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> tuple[TestClient, Registry]:
    from jarvis.agentic_ide import recents
    from jarvis.ui.web import agentic_ide_routes

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    registry = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "get_registry", lambda: registry)
    monkeypatch.setattr(agentic_ide_routes, "get_registry", lambda: registry)
    app = FastAPI()
    app.include_router(agentic_ide_routes.router)
    return TestClient(app), registry


def _open(client: TestClient, folder: Path, **extra: Any) -> Any:
    return client.post(
        "/api/agentic-ide/session",
        json={"folder": str(folder), "terminals": [{"agent": "claude"}], **extra},
    )


def test_a_workspace_on_a_computer_reports_what_stayed_here(
    client: tuple[TestClient, Registry], tmp_path: Path
) -> None:
    http, registry = client

    response = _open(http, tmp_path, computer_id="c_1")

    assert response.status_code == 200, response.text
    assert ".env" in response.json()["message"]
    [workspace] = registry.sessions
    assert workspace.terminals[0].computer_id == "c_1"


def test_omitted_follows_the_workspace_and_null_means_this_machine(
    client: tuple[TestClient, Registry], tmp_path: Path
) -> None:
    http, registry = client
    workspace_id = _open(http, tmp_path, computer_id="c_1").json()["session"]["id"]

    followed = http.post("/api/agentic-ide/terminals", json={"workspace_id": workspace_id})
    here = http.post(
        "/api/agentic-ide/terminals", json={"workspace_id": workspace_id, "computer_id": None}
    )

    assert followed.status_code == 200 and here.status_code == 200
    assert followed.json()["terminal"]["computer_id"] == "c_1"
    assert here.json()["terminal"]["computer_id"] == ""


def test_a_server_that_cannot_take_the_panes_answers_502_and_keeps_nothing(
    client: tuple[TestClient, Registry], far: RemotePool, tmp_path: Path
) -> None:
    http, registry = client
    far.missing = "missing:Claude Code\n"

    response = _open(http, tmp_path, computer_id="c_1")

    assert response.status_code == 502
    assert "not installed" in response.json()["detail"]
    assert registry.sessions == []
