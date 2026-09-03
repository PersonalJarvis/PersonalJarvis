"""Onboarding: the lead offers a team ONCE, the person picks, only the picked
teammates are created."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.society.runtime import SocietyRuntime
from jarvis.ui.web.society_routes import router


def _tool(name: str, desc: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, description=desc, risk_tier="monitor")


TOOLS = {
    "gmail": _tool("gmail", "Read and send mail."),
    "cli_gh": _tool("cli_gh", "GitHub CLI."),
}


def _client(tmp_path: Path) -> tuple[TestClient, SocietyRuntime]:
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False, brain_tools=lambda: TOOLS)
    app = FastAPI()
    app.include_router(router)
    app.state.society_factory = lambda: runtime
    return TestClient(app), runtime


def test_the_first_open_proposes_a_team_once(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        first = c.post("/api/society/onboarding/start").json()
        assert first["offered"] is True
        names = first["proposal"]["action"]["payload"]["names"]
        assert "Mailbox" in names and "Repo" in names
        assert c.get("/api/society/status").json()["onboarding_done"] is True
        again = c.post("/api/society/onboarding/start").json()
        assert again == {"offered": False, "reason": "already_offered"}
        assert c.get("/api/society/proposals").json()["total"] == 1


def test_confirming_creates_only_the_picked_teammates(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        item = c.post("/api/society/onboarding/start").json()["proposal"]
        out = c.post(
            f"/api/society/proposals/{item['id']}/resolve",
            json={"approve": True, "note": "Mailbox"},
        ).json()
        assert out["applied"] is True and "mailbox" in out["detail"]
        names = {a["name"] for a in c.get("/api/society/agents").json()["agents"]}
        assert "Mailbox" in names and "Repo" not in names


def test_a_roster_that_already_has_teammates_is_never_asked(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client as c:
        c.post("/api/society/agents", json={"name": "Scout"})
        body = c.post("/api/society/onboarding/start").json()
        assert body == {"offered": False, "reason": "roster_not_empty"}
        assert c.get("/api/society/proposals").json()["total"] == 0
