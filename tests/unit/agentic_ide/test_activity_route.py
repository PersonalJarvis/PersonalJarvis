"""`GET /api/agentic-ide/activity` — the status badge's fast poll.

The route exists so the badge can be polled every second or two without
dragging the recap summarizer along, so the properties pinned here are the
contract ones: it answers for the workspace asked for, it never raises when the
workspace is gone, and it reports the SAME reading the recap poll reports —
two polls disagreeing about one pane is worse than either being slow.
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import recap_engine
from jarvis.agentic_ide import session as ide
from jarvis.ui.web import agentic_ide_routes


@pytest.fixture
def client() -> TestClient:
    ide.reset_registry()
    recap_engine.reset_for_tests()
    app = FastAPI()
    app.include_router(agentic_ide_routes.router)
    with TestClient(app) as test_client:
        yield test_client
    ide.reset_registry()
    recap_engine.reset_for_tests()


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


def test_activity_reports_every_pane_of_the_open_workspace(client, tmp_path) -> None:
    session = _workspace(tmp_path)
    term = session.terminals[0]
    term.status = "live"
    term.pty_id = "pty-1"
    term.last_submit_at = time.time()

    body = client.get("/api/agentic-ide/activity").json()

    assert body["workspace_id"] == session.id
    assert len(body["terminals"]) == 1
    row = body["terminals"][0]
    assert row["name"] == "Mika"
    assert row["status"] == "live"
    # An instruction was submitted, so the pane has work behind it — the half
    # that separates "finished" from "never asked for anything".
    assert row["worked"] is True
    assert row["activity"] in {"working", "waiting", "asking"}


def test_the_fast_poll_and_the_recap_poll_agree(client, tmp_path) -> None:
    session = _workspace(tmp_path)
    term = session.terminals[0]
    term.status = "live"
    term.pty_id = "pty-1"

    fast = client.get("/api/agentic-ide/activity").json()["terminals"][0]
    slow = client.get("/api/agentic-ide/recaps").json()["terminals"][0]

    assert fast["activity"] == slow["activity"]
    assert fast["worked"] == slow["worked"]
    assert fast["status"] == slow["status"]


def test_a_gone_workspace_answers_empty_rather_than_erroring(client, tmp_path) -> None:
    _workspace(tmp_path)

    body = client.get("/api/agentic-ide/activity", params={"workspace_id": "nope"}).json()

    assert body == {"workspace_id": None, "terminals": []}
