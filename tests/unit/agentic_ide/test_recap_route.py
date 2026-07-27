"""`GET /api/agentic-ide/recaps` — the read the pane headers poll.

It runs several times a minute per open workspace, so the properties worth
pinning are the boring ones: it answers for the workspace asked for, it never
raises when the workspace is gone, and it says the same thing the state payload
says (two surfaces disagreeing about what a pane is doing is worse than either
of them being silent).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import session as ide
from jarvis.ui.web import agentic_ide_routes


@pytest.fixture
def client() -> TestClient:
    ide.reset_registry()
    app = FastAPI()
    app.include_router(agentic_ide_routes.router)
    with TestClient(app) as test_client:
        yield test_client
    ide.reset_registry()


def _workspace(tmp_path, name: str = "Mika") -> ide.Session:
    """One open workspace holding a single pane, without spawning anything."""
    registry = ide.get_registry()
    session = ide.Session(
        id="ide_test",
        folder=str(tmp_path),
        name="Test",
        profile=ide.probe_project(tmp_path),
        terminals=[
            ide.Terminal(
                key=name.lower(),
                name=name,
                agent="claude",
                display_name="Claude Code",
                index=0,
            )
        ],
        created_at=0.0,
    )
    registry._sessions[session.id] = session  # noqa: SLF001 - no spawn in a unit test
    registry._active = session.id  # noqa: SLF001
    return session


def test_recaps_describe_every_pane_of_the_open_workspace(client, tmp_path) -> None:
    session = _workspace(tmp_path)
    term = session.terminals[0]
    term.status = "live"
    term.last_prompt = "Fix the failing login test"
    term.transcript.feed("Running pytest tests/unit/test_login.py\r\n")

    body = client.get("/api/agentic-ide/recaps").json()

    assert body["workspace_id"] == session.id
    assert len(body["terminals"]) == 1
    row = body["terminals"][0]
    assert row["name"] == "Mika"
    assert row["status"] == "live"
    assert row["recap"] == "Running pytest tests/unit/test_login.py"
    assert "Fix the failing login test" in row["recap_detail"]


def test_the_route_and_the_state_payload_agree(client, tmp_path) -> None:
    session = _workspace(tmp_path)
    session.terminals[0].status = "live"
    session.terminals[0].transcript.feed("Reading jarvis/core/config.py\r\n")

    polled = client.get("/api/agentic-ide/recaps").json()["terminals"][0]
    state = client.get("/api/agentic-ide/state").json()["session"]["terminals"][0]

    assert polled["recap"] == state["recap"]
    assert polled["recap_detail"] == state["recap_detail"]


def test_an_unknown_workspace_answers_empty_rather_than_404(client, tmp_path) -> None:
    """A poll outliving the workspace it started for is normal, not an error."""
    _workspace(tmp_path)

    response = client.get("/api/agentic-ide/recaps?workspace_id=ide_gone")

    assert response.status_code == 200
    assert response.json() == {"workspace_id": None, "terminals": []}


def test_no_workspace_at_all_is_an_empty_answer(client) -> None:
    response = client.get("/api/agentic-ide/recaps")

    assert response.status_code == 200
    assert response.json()["terminals"] == []
