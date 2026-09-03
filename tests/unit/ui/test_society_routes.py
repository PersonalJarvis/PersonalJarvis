"""The society REST surface, headless: a description edit keeps what the agent
earned in its chat, and a configuration proposal is applied only by its own route."""

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
    "google-calendar": _tool("google-calendar", "Read the calendar."),
    "cli_gh": _tool("cli_gh", "GitHub CLI."),
}


def _app(tmp_path: Path) -> tuple[FastAPI, SocietyRuntime]:
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False, brain_tools=lambda: TOOLS)
    app = FastAPI()
    app.include_router(router)
    app.state.society_factory = lambda: runtime
    return app, runtime


def test_a_description_patch_keeps_the_focus_and_rules_the_agent_earned(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        created = c.post(
            "/api/society/agents",
            json={
                "name": "Mailbox",
                "title": "Gmail agent",
                "description": "Handle my mail.",
                "focus": ["cli:gh", "plugin:gmail"],
                "approval_rules": {"require_approval": [], "always_allow": ["plugin:gmail:send"]},
            },
        ).json()["agent"]
        assert created["focus"] == ["cli:gh", "plugin:gmail"]

        # A prose edit that mentions the calendar: focus grows, order and rules stay.
        patched = c.patch(
            "/api/society/agents/mailbox",
            json={
                "description": "Handle my mail and my calendar; external mail only after approval."
            },
        ).json()["agent"]
        assert patched["focus"][:2] == ["cli:gh", "plugin:gmail"]
        assert "plugin:google-calendar" in patched["focus"]
        assert patched["approval_rules"] == {
            "require_approval": [],
            "always_allow": ["plugin:gmail:send"],
        }

        # An agent with NO rules still gets the derived ones from an approval clause.
        c.post("/api/society/agents", json={"name": "Planner", "title": "Calendar agent"})
        planner = c.patch(
            "/api/society/agents/planner",
            json={"description": "Manage my calendar; invitations only after approval."},
        ).json()["agent"]
        assert planner["approval_rules"]["require_approval"]


def test_a_proposal_is_applied_only_by_its_own_route(tmp_path: Path) -> None:
    app, runtime = _app(tmp_path)
    with TestClient(app) as c:
        c.post("/api/society/agents", json={"name": "Mailbox", "description": "Handle my mail."})
        # Park a proposal the way the tool does (validated payload, config capability).
        item = c.post(
            "/api/society/approvals",
            json={
                "agent_id": "mailbox",
                "capability": "core:config:rule",
                "action": {"kind": "rule", "payload": {"text": "Be brief."}, "reason": "t"},
                "summary": "Add a standing rule: Be brief.",
            },
        ).json()["approval"]
        listed = c.get("/api/society/proposals").json()
        assert [p["id"] for p in listed["proposals"]] == [item["id"]]
        generic = c.post(f"/api/society/approvals/{item['id']}/resolve", json={"approve": True})
        assert generic.status_code == 409
        resolved = c.post(f"/api/society/proposals/{item['id']}/resolve", json={"approve": True})
        assert resolved.status_code == 200, resolved.text
        body = resolved.json()
        assert body["applied"] is True and body["kind"] == "rule"
        agent = c.get("/api/society/agents/mailbox").json()["agent"]
        assert agent["description"] == "Handle my mail.\n\nBe brief."
        assert c.get("/api/society/proposals").json()["total"] == 0
        again = c.post(f"/api/society/proposals/{item['id']}/resolve", json={"approve": True})
        assert again.status_code == 409
        missing = c.post("/api/society/proposals/nope/resolve", json={"approve": True})
        assert missing.status_code == 404
    assert runtime is not None
