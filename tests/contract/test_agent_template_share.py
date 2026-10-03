"""Share an agent, publish it, install it elsewhere — headless, no keys, no network.

The round trip a person makes: the Share sheet reads the scrubbed template,
saves edits, publishes (the GitHub call is faked), and a second install
creates a NEW teammate from the published template — on its own model, with
nothing of the author's machine attached.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.marketplace import publish
from jarvis.society.runtime import SocietyRuntime
from jarvis.ui.web.society_routes import router

SECRET = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def _app(tmp_path: Path) -> tuple[FastAPI, SocietyRuntime]:
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    app = FastAPI()
    app.include_router(router)
    app.state.society_factory = lambda: runtime
    return app, runtime


@pytest.fixture
def author(tmp_path: Path):
    app, runtime = _app(tmp_path / "author")
    with TestClient(app) as client:
        try:
            created = client.post(
                "/api/society/agents",
                json={
                    "name": "Release Scribe",
                    "title": "Writes the release notes",
                    "description": (
                        "Collect merged pull requests and write release notes.\n"
                        f"Use the token {SECRET} and mail drafts to me@private.dev."
                    ),
                    "account_id": "seat-1",
                    "daily_budget_usd": 40,
                    "approval_rules": {
                        "require_approval": ["plugin:github:merge"],
                        "always_allow": ["core:shell"],
                    },
                },
            )
            assert created.status_code == 200, created.text
            yield client, created.json()["agent"]["agent_id"]
        finally:
            client.portal.call(runtime.close)


def test_share_sheet_shows_the_scrubbed_template(author) -> None:
    client, agent_id = author
    body = client.get(f"/api/society/agents/{agent_id}/template").json()
    template = body["template"]
    assert SECRET not in str(body)
    assert "me@private.dev" not in str(body)
    assert {f["kind"] for f in body["findings"]} == {"secret", "email"}
    assert "seat-1" not in str(template)
    assert template["require_approval"] == ["plugin:github:merge"]
    assert body["submission"]["kind"] == "agent"
    assert body["listing"]["name"] == "release-scribe"
    assert body["errors"] == []


def test_edits_save_and_reset(author) -> None:
    client, agent_id = author
    url = f"/api/society/agents/{agent_id}/template"
    edited = client.put(url, json={"summary": "Turns merged PRs into notes.", "version": "2.0.0"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["listing"]["description"] == "Turns merged PRs into notes."
    assert edited.json()["listing"]["version"] == "2.0.0"
    reset = client.put(url, json={"reset": True}).json()
    assert reset["listing"]["version"] == "1.0.0"
    # The agent itself never changed.
    agent = client.get(f"/api/society/agents/{agent_id}").json()["agent"]
    assert SECRET in agent["description"]


def test_publish_files_the_built_template(author, monkeypatch: pytest.MonkeyPatch) -> None:
    client, agent_id = author
    seen: dict[str, Any] = {}

    async def fake_submit(normalized: dict[str, Any]) -> dict[str, Any]:
        seen.update(normalized)
        return {
            "issue_url": "https://github.com/PersonalJarvis/marketplace/issues/7",
            "issue_number": 7,
            "submission_path": "submissions/release-scribe.json",
        }

    monkeypatch.setattr(publish, "submit", fake_submit)
    resp = client.post(f"/api/society/agents/{agent_id}/template/publish")
    assert resp.status_code == 200, resp.text
    assert resp.json()["install"]["cli"] == "jarvis marketplace install release-scribe"
    assert seen["kind"] == "agent" and SECRET not in str(seen)
    # The next share starts from the next version.
    after = client.get(f"/api/society/agents/{agent_id}/template").json()
    assert after["published"]["version"] == "1.0.0"
    assert after["listing"]["version"] == "1.0.1"


def test_the_lead_is_not_shareable(tmp_path: Path) -> None:
    app, runtime = _app(tmp_path)
    with TestClient(app) as client:
        try:
            resp = client.get("/api/society/agents/jarvis/template")
            assert resp.status_code in (404, 409)
        finally:
            client.portal.call(runtime.close)


def test_install_creates_a_new_safe_teammate(author, tmp_path: Path) -> None:
    client, agent_id = author
    published = client.get(f"/api/society/agents/{agent_id}/template").json()["submission"]

    app, runtime = _app(tmp_path / "installer")
    with TestClient(app) as other:
        try:
            first = other.post("/api/society/templates/install", json={"template": published})
            assert first.status_code == 200, first.text
            agent = first.json()["agent"]
            assert agent["name"] == "Release Scribe"
            assert agent["account_id"] == ""
            assert agent["daily_budget_usd"] == 2.0
            assert agent["approval_rules"]["always_allow"] == []
            assert agent["approval_rules"]["require_approval"] == ["plugin:github:merge"]
            assert agent["approval_mode"] in ("ask", "bypass")
            assert SECRET not in agent["description"]

            second = other.post(
                "/api/society/templates/install", json={"template": published["agent"]}
            )
            assert second.status_code == 200, second.text
            assert second.json()["agent"]["name"] == "Release Scribe 2"
            assert second.json()["renamed_from"] == "Release Scribe"

            bad = other.post(
                "/api/society/templates/install",
                json={"template": {**published["agent"], "approval_mode": "bypass"}},
            )
            assert bad.status_code == 422
        finally:
            other.portal.call(runtime.close)
