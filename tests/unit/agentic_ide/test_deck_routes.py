"""The report lane the Command Deck renders, and answering from it.

Voice and click go through the same two routes on purpose: "tell me about Nova"
and pressing Nova's card are the same instruction, and a second path for the
second one is a second thing to keep in step.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import standup
from jarvis.ui.web import agentic_ide_routes

from .test_standup import entry


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    standup.reset()
    standup.unwire()
    yield
    standup.reset()
    standup.unwire()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(agentic_ide_routes.router)
    return TestClient(app)


def filled(*panes: str, kind: str = "completed") -> standup.StandupQueue:
    queue = standup.queue()
    queue.conversation_started()
    queue.offer([entry(pane, kind) for pane in panes])
    return queue


def test_an_empty_lane_reads_as_empty_rather_than_missing(client: TestClient) -> None:
    body = client.get("/api/agentic-ide/deck/queue").json()

    assert body["pending"] == []
    assert body["on_air"] is None
    assert body["sleeping"] is False


def test_the_lane_lists_what_is_waiting_blockers_first(client: TestClient) -> None:
    filled("Mika")
    standup.queue().offer([entry("Nova", "needs_input")])

    body = client.get("/api/agentic-ide/deck/queue").json()

    assert [r["pane"] for r in body["pending"]] == ["Nova", "Mika"]


def test_asking_for_one_report_puts_it_on_air(client: TestClient) -> None:
    queue = filled("Mika", "Nova")
    queue.take_due()
    nova = next(r for r in queue.pending() if r.pane == "Nova")

    body = client.post(
        "/api/agentic-ide/deck/ack", json={"id": nova.id, "action": "next"}
    ).json()

    assert body["found"] is True
    assert body["on_air"]["pane"] == "Nova"


def test_later_puts_the_lane_to_sleep_without_losing_anything(client: TestClient) -> None:
    queue = filled("Mika", "Nova")
    queue.take_due()
    first = queue.pending()[0]

    body = client.post(
        "/api/agentic-ide/deck/ack", json={"id": first.id, "action": "later"}
    ).json()

    assert body["sleeping"] is True
    assert len(body["pending"]) == 2


def test_dropping_one_takes_it_out_of_the_lane(client: TestClient) -> None:
    queue = filled("Mika", "Nova")
    mika = next(r for r in queue.pending() if r.pane == "Mika")

    body = client.post(
        "/api/agentic-ide/deck/ack", json={"id": mika.id, "action": "drop"}
    ).json()

    assert [r["pane"] for r in body["pending"]] == ["Nova"]


def test_answering_a_report_that_is_already_gone_is_not_an_error(
    client: TestClient,
) -> None:
    # A report goes away when its pane does, and a click that lands a moment
    # later got the outcome it wanted. A 404 here would put an error toast in
    # front of a user who did nothing wrong.
    response = client.post(
        "/api/agentic-ide/deck/ack", json={"id": "sr-gone", "action": "next"}
    )

    assert response.status_code == 200
    assert response.json()["found"] is False


def test_an_action_nobody_defined_is_refused_at_the_door(client: TestClient) -> None:
    queue = filled("Mika")
    mika = queue.pending()[0]

    response = client.post(
        "/api/agentic-ide/deck/ack", json={"id": mika.id, "action": "snooze"}
    )

    assert response.status_code == 422


def test_the_route_literal_still_matches_the_source_of_truth() -> None:
    """Layer 3 against layer 0, same guard as the workspace-view enum."""
    from typing import get_args

    assert set(get_args(agentic_ide_routes.DeckAction)) == set(get_args(standup.Action))
