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
    assert "Fix the failing login test" in row["recap"]
    assert "Running pytest tests/unit/test_login.py" in row["recap_detail"]
    # Nothing was summarized by a model here, and the answer says so rather
    # than letting the fallback pass itself off as the real thing.
    assert row["source"] == "heuristic"


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


def test_a_thin_recap_says_why_it_is_thin(client, tmp_path) -> None:
    """The complaint this field answers: "the recaps say nothing and nobody
    knows why". A pane with four printed rows is below the bar for a summary,
    and the payload now says so instead of leaving it a mystery."""
    session = _workspace(tmp_path)
    session.terminals[0].status = "live"
    session.terminals[0].transcript.feed("Reading jarvis/core/config.py\r\n")

    row = client.get("/api/agentic-ide/recaps").json()["terminals"][0]

    assert row["source"] == "heuristic"
    assert row["reason"] == "warming"


# --------------------------------------------------------------------------- #
# Writing a recap yourself                                                     #
# --------------------------------------------------------------------------- #
def test_a_hand_written_recap_replaces_the_derived_one(client, tmp_path) -> None:
    session = _workspace(tmp_path)
    session.terminals[0].status = "live"
    session.terminals[0].last_prompt = "Fix the failing login test"

    written = client.put(
        "/api/agentic-ide/terminals/Mika/recap",
        json={
            "recap": "Demo branch — leave alone",
            "recap_detail": "Recording for the Thursday walkthrough.",
        },
    ).json()

    assert written["recap"] == "Demo branch — leave alone"
    assert written["source"] == "user"
    assert written["reason"] == "pinned"
    # And it is what every other reader of this pane sees, not a second opinion
    # living beside the real one.
    polled = client.get("/api/agentic-ide/recaps").json()["terminals"][0]
    assert polled["recap"] == "Demo branch — leave alone"
    assert polled["recap_detail"] == "Recording for the Thursday walkthrough."
    state = client.get("/api/agentic-ide/state").json()["session"]["terminals"][0]
    assert state["recap"] == "Demo branch — leave alone"


def test_a_hand_written_recap_is_not_summarized_over(client, tmp_path) -> None:
    """A pane the user has labelled must not have a model spend a request to
    overwrite the label."""
    session = _workspace(tmp_path)
    term = session.terminals[0]
    term.status = "live"
    for index in range(20):
        term.transcript.feed(f"Step {index}: doing real work\r\n")

    client.put(
        "/api/agentic-ide/terminals/Mika/recap",
        json={"recap": "Demo branch — leave alone"},
    )
    calls: list[object] = []

    async def _never(*args: object, **kwargs: object) -> None:
        calls.append(args)
        return None

    original = recap_engine.summarize_with_model
    recap_engine.summarize_with_model = _never  # type: ignore[assignment]
    try:
        client.get("/api/agentic-ide/recaps")
    finally:
        recap_engine.summarize_with_model = original  # type: ignore[assignment]

    assert calls == []


def test_clearing_hands_the_pane_back_to_the_automatic_recap(client, tmp_path) -> None:
    session = _workspace(tmp_path)
    session.terminals[0].status = "live"
    session.terminals[0].last_prompt = "Fix the failing login test"
    client.put(
        "/api/agentic-ide/terminals/Mika/recap",
        json={"recap": "Demo branch — leave alone"},
    )

    cleared = client.delete("/api/agentic-ide/terminals/Mika/recap").json()

    assert cleared["source"] == "heuristic"
    assert "Fix the failing login test" in cleared["recap"]


def test_saving_an_empty_recap_clears_it(client, tmp_path) -> None:
    """Selecting the text and deleting it means "go back to automatic"."""
    session = _workspace(tmp_path)
    session.terminals[0].status = "live"
    session.terminals[0].last_prompt = "Fix the failing login test"
    client.put(
        "/api/agentic-ide/terminals/Mika/recap",
        json={"recap": "Demo branch — leave alone"},
    )

    answer = client.put(
        "/api/agentic-ide/terminals/Mika/recap", json={"recap": "   "}
    ).json()

    assert answer["source"] == "heuristic"
    assert recap_engine.is_pinned("mika") is False


def test_editing_an_unknown_terminal_is_a_404(client, tmp_path) -> None:
    _workspace(tmp_path)

    response = client.put(
        "/api/agentic-ide/terminals/Nobody/recap", json={"recap": "Anything"}
    )

    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Summarizing on demand                                                        #
# --------------------------------------------------------------------------- #
def test_refresh_returns_the_fresh_summary(client, tmp_path, monkeypatch) -> None:
    session = _workspace(tmp_path)
    term = session.terminals[0]
    term.status = "live"
    for index in range(20):
        term.transcript.feed(f"Step {index}: doing real work\r\n")

    async def _summary(*args: object, **kwargs: object) -> recap_engine.SmartRecap:
        return recap_engine.SmartRecap(
            headline="Rewrote the auth middleware",
            detail="Two tests still fail.",
            source=recap_engine.BY_MODEL,
            reason=recap_engine.WHY_SUMMARIZED,
            writer="test-model",
        )

    monkeypatch.setattr(recap_engine, "summarize_with_model", _summary)

    answer = client.post("/api/agentic-ide/terminals/Mika/recap/refresh").json()

    assert answer["recap"] == "Rewrote the auth middleware"
    assert answer["source"] == "model"
    assert answer["writer"] == "test-model"


def test_refresh_without_a_reachable_model_degrades_honestly(
    client, tmp_path, monkeypatch
) -> None:
    """§3: an install with no key still gets a working pane and an honest
    answer, never a 500 and never a red pane."""
    session = _workspace(tmp_path)
    term = session.terminals[0]
    term.status = "live"
    term.last_prompt = "Fix the failing login test"
    for index in range(20):
        term.transcript.feed(f"Step {index}: doing real work\r\n")

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("no API key configured for key=sk-live-1234")

    monkeypatch.setattr(recap_engine, "summarize_with_model", _boom)

    response = client.post("/api/agentic-ide/terminals/Mika/recap/refresh")

    assert response.status_code == 200
    answer = response.json()
    assert answer["source"] == "heuristic"
    assert answer["reason"] == "unavailable"
    assert "RuntimeError" in answer["note"]
    # AP-2/AP-12: this string is rendered on screen and could be screenshotted.
    assert "sk-live-1234" not in answer["note"]
    assert "Fix the failing login test" in answer["recap"]


def test_refresh_drops_a_hand_written_recap(client, tmp_path, monkeypatch) -> None:
    """Asking for a fresh summary of a pane you labelled yourself is asking for
    the automatic label back — which is what the button says it does."""
    session = _workspace(tmp_path)
    term = session.terminals[0]
    term.status = "live"
    term.last_prompt = "Fix the failing login test"
    client.put(
        "/api/agentic-ide/terminals/Mika/recap",
        json={"recap": "Demo branch — leave alone"},
    )

    async def _nothing(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(recap_engine, "summarize_with_model", _nothing)

    answer = client.post("/api/agentic-ide/terminals/Mika/recap/refresh").json()

    assert answer["source"] != "user"
    assert recap_engine.is_pinned("mika") is False
