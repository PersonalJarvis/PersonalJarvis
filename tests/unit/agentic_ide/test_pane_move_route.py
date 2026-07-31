"""`POST /api/agentic-ide/terminals/{name}/move` — the drop the grid reports.

The placement arithmetic is pinned in ``test_pane_move.py``; what this file is
about is the contract a client depends on: the answer carries the workspace as
it now is (so the grid can redraw without a second read), and the three ways a
move can be refused arrive as three different status codes — a caller that gets
one code for "no such pane" and "no such direction" cannot tell the user which
of the two to fix.
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


def _workspace(tmp_path, names: tuple[str, ...] = ("Mika", "Nova")) -> ide.Session:
    """An open workspace of side-by-side panes, without spawning anything."""
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
                index=index,
                column=index,
                slot=0,
            )
            for index, name in enumerate(names)
        ],
        created_at=0.0,
    )
    registry._sessions[session.id] = session  # noqa: SLF001 - no spawn in a unit test
    registry._active = session.id  # noqa: SLF001
    return session


def _layout(body: dict) -> list[tuple[str, int, int]]:
    return [
        (term["name"], term["column"], term["slot"])
        for term in body["state"]["session"]["terminals"]
    ]


def test_a_swap_answers_with_the_rearranged_workspace(client, tmp_path) -> None:
    _workspace(tmp_path)

    reply = client.post(
        "/api/agentic-ide/terminals/Mika/move",
        json={"target": "Nova", "position": "swap"},
    )

    assert reply.status_code == 200
    body = reply.json()
    assert body["moved"] == "Mika"
    assert body["target"] == "Nova"
    # The whole point of returning state: the grid redraws from this alone.
    assert _layout(body) == [("Nova", 0, 0), ("Mika", 1, 0)]


def test_position_defaults_to_swap(client, tmp_path) -> None:
    """The plain "these two belong the other way round" drop needs no argument."""
    _workspace(tmp_path)

    body = client.post(
        "/api/agentic-ide/terminals/Mika/move", json={"target": "Nova"}
    ).json()

    assert body["position"] == "swap"
    assert _layout(body) == [("Nova", 0, 0), ("Mika", 1, 0)]


def test_a_pane_can_be_moved_into_another_columns_stack(client, tmp_path) -> None:
    _workspace(tmp_path, ("Mika", "Nova", "Robin"))

    body = client.post(
        "/api/agentic-ide/terminals/Robin/move",
        json={"target": "Mika", "position": "below"},
    ).json()

    assert _layout(body) == [("Mika", 0, 0), ("Robin", 0, 1), ("Nova", 1, 0)]


def test_an_unknown_pane_is_a_404_that_names_the_real_ones(client, tmp_path) -> None:
    _workspace(tmp_path)

    reply = client.post(
        "/api/agentic-ide/terminals/Gandalf/move",
        json={"target": "Nova", "position": "swap"},
    )

    assert reply.status_code == 404
    assert "Mika, Nova" in reply.json()["detail"]


def test_an_impossible_position_is_a_422(client, tmp_path) -> None:
    """A different fix from a wrong name, so a different status."""
    _workspace(tmp_path)

    reply = client.post(
        "/api/agentic-ide/terminals/Mika/move",
        json={"target": "Nova", "position": "diagonally"},
    )

    assert reply.status_code == 422
    assert "Position must be" in reply.json()["detail"]


def test_moving_without_an_open_workspace_is_a_409(client) -> None:
    reply = client.post(
        "/api/agentic-ide/terminals/Mika/move",
        json={"target": "Nova", "position": "swap"},
    )

    assert reply.status_code == 409
