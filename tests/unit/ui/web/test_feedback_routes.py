"""Tests for the in-app feedback REST endpoint (finding 13, AP-23 wave 2).

Contract (see jarvis/ui/web/feedback_routes.py):
- POST /api/feedback -> {"ok": bool, "status": str, "detail": str, "github_url": str|None}

When no Discord webhook is configured (the common case for every downloader —
``discord_feedback_webhook_url`` is a maintainer-only operator credential that
was never shipped), the endpoint must degrade HONESTLY: point the end user at
the project's public GitHub issues page instead of instructing them to
configure a credential that is meaningless for them.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

GITHUB_ISSUES_URL = "https://github.com/PersonalJarvis/PersonalJarvis/issues"


def _client() -> TestClient:
    from jarvis.ui.web.feedback_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def client() -> TestClient:
    return _client()


def _payload(**overrides: object) -> dict:
    body = {
        "type": "bug",
        "title": "Something broke",
        "description": "It broke when I clicked the button.",
    }
    body.update(overrides)
    return body


def test_no_webhook_configured_points_to_github_issues(client: TestClient, monkeypatch) -> None:
    """No webhook configured -> honest downloader-facing fallback: a GitHub
    issues URL, not an instruction to set an operator-only credential."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    monkeypatch.setattr(feedback_routes, "get_secret", lambda *a, **k: None)

    resp = client.post("/api/feedback", json=_payload())

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    # The response must carry a URL the frontend can render as "report it on
    # GitHub" rather than dead-ending the user.
    assert body.get("github_url") == GITHUB_ISSUES_URL
    assert GITHUB_ISSUES_URL in body["detail"]


def test_no_webhook_configured_does_not_instruct_setting_a_credential(
    client: TestClient, monkeypatch
) -> None:
    """The old behavior told the END USER to set a Discord webhook credential
    ('discord_feedback_webhook_url') — meaningless for a downloader who is not
    the project operator. That misdirection must be gone."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    monkeypatch.setattr(feedback_routes, "get_secret", lambda *a, **k: None)

    resp = client.post("/api/feedback", json=_payload())

    detail_lower = resp.json()["detail"].lower()
    assert "discord_feedback_webhook_url" not in detail_lower
    assert "environment variable" not in detail_lower
    assert "credential" not in detail_lower
