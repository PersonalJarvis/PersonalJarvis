"""The bell's REST surface — the CLI-first half (CLAUDE.md §5).

The panel in the header is one client of this. The other is a terminal: the
same three questions ("what stopped?", "stop counting these", "throw them
away") have to be answerable from `jarvis api agentic-ide ...` without the
desktop app being open, which is exactly the situation somebody checking on a
long-running fleet is in.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import notifications


@pytest.fixture(autouse=True)
def _clean_store():
    notifications.reset()
    yield
    notifications.reset()


@pytest.fixture
def client() -> TestClient:
    from jarvis.ui.web.agentic_ide_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _fill(count: int = 2) -> None:
    for index in range(count):
        notifications.center().add(
            notifications.Notification(
                id=f"n{index}",
                kind="completed",
                workspace_id="ws1",
                workspace="Demo",
                pane_key=f"t{index}",
                pane=f"T{index}",
                agent="claude",
                display_name="Claude Code",
                title="Finished and waiting at its prompt",
                detail="Rewrite the ranking pipeline",
                created_at=float(index),
            )
        )


def test_an_empty_bell_is_a_normal_answer(client: TestClient) -> None:
    """Nothing has stopped. That is a 200 with an empty list, never an error."""
    body = client.get("/api/agentic-ide/notifications").json()

    assert body["unread"] == 0
    assert body["notifications"] == []


def test_it_lists_newest_first_with_everything_a_jump_needs(client: TestClient) -> None:
    """A call-sign counts from T1 in EVERY workspace, so the id has to travel."""
    _fill()

    body = client.get("/api/agentic-ide/notifications").json()

    assert [entry["id"] for entry in body["notifications"]] == ["n1", "n0"]
    assert body["unread"] == 2
    first = body["notifications"][0]
    assert first["workspace_id"] == "ws1"
    assert first["pane"] == "T1"
    assert first["kind"] == "completed"


def test_reading_clears_the_count_and_keeps_the_list(client: TestClient) -> None:
    _fill()

    marked = client.post("/api/agentic-ide/notifications/read", json={"ids": []}).json()

    assert marked["changed"] == 2
    assert marked["unread"] == 0
    body = client.get("/api/agentic-ide/notifications").json()
    assert len(body["notifications"]) == 2
    assert all(entry["read"] for entry in body["notifications"])


def test_one_entry_can_be_read_on_its_own(client: TestClient) -> None:
    _fill()

    marked = client.post("/api/agentic-ide/notifications/read", json={"ids": ["n0"]}).json()

    assert marked["changed"] == 1
    assert marked["unread"] == 1


def test_discarding_one_leaves_the_rest(client: TestClient) -> None:
    _fill()

    dropped = client.delete("/api/agentic-ide/notifications/n0").json()

    assert dropped["changed"] == 1
    body = client.get("/api/agentic-ide/notifications").json()
    assert [entry["id"] for entry in body["notifications"]] == ["n1"]


def test_discarding_something_that_is_already_gone_is_quiet(client: TestClient) -> None:
    """It may have left with its workspace between the read and the click."""
    _fill()

    dropped = client.delete("/api/agentic-ide/notifications/nope")

    assert dropped.status_code == 200
    assert dropped.json()["changed"] == 0


def test_discarding_everything_empties_the_bell(client: TestClient) -> None:
    _fill(4)

    dropped = client.delete("/api/agentic-ide/notifications").json()

    assert dropped["changed"] == 4
    assert client.get("/api/agentic-ide/notifications").json()["notifications"] == []


def test_the_switch_is_reported_so_a_quiet_bell_is_explainable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Nothing is arriving" and "collection is off" must not look the same."""
    monkeypatch.setattr(notifications, "enabled", lambda: False)

    assert client.get("/api/agentic-ide/notifications").json()["enabled"] is False
