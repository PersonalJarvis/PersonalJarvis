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

from pathlib import Path

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


# ----------------------------------------------------------------------
# GET /api/feedback/status — the capability probe the form renders from
# ----------------------------------------------------------------------


def test_status_not_configured_offers_github_fallback(client: TestClient, monkeypatch) -> None:
    """Fresh install (no webhook) -> configured=False plus everything the
    frontend needs to compose a prefilled GitHub issue instead."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    monkeypatch.setattr(feedback_routes, "get_secret", lambda *a, **k: None)

    resp = client.get("/api/feedback/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["github_url"] == GITHUB_ISSUES_URL
    # The same system fields the POST route would attach server-side.
    assert set(body["context"]) == {"app_version", "os", "python"}
    assert all(isinstance(v, str) and v for v in body["context"].values())


def test_status_configured_never_leaks_the_webhook_url(client: TestClient, monkeypatch) -> None:
    """Operator install (webhook present) -> configured=True; the webhook URL
    itself (an operator credential) must never appear in the response."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    webhook_url = "https://discord.com/api/webhooks/123/abc"  # test dummy
    monkeypatch.setattr(feedback_routes, "get_secret", lambda *a, **k: webhook_url)

    resp = client.get("/api/feedback/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert webhook_url not in resp.text


def test_empty_screenshot_payload_sends_no_attachment(client: TestClient, monkeypatch) -> None:
    """A data-URL without a base64 payload decodes to b"" — the dispatch must
    fall back to the plain JSON webhook call instead of attaching an empty
    file to Discord."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    captured: dict = {}

    class _FakeResponse:
        is_success = True
        status_code = 204
        text = ""

    class _FakeAsyncClient:
        def __init__(self, timeout: float | None = None) -> None:
            pass

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def post(self, url: str, **kwargs: object) -> _FakeResponse:
            captured.update({"url": url, **kwargs})
            return _FakeResponse()

    monkeypatch.setattr(
        feedback_routes, "get_secret",
        lambda *a, **k: "https://discord.com/api/webhooks/123/abc",
    )
    monkeypatch.setattr(feedback_routes.httpx, "AsyncClient", _FakeAsyncClient)

    resp = client.post(
        "/api/feedback", json=_payload(screenshot="data:image/png;base64,")
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"
    # Plain JSON dispatch, no multipart upload of a zero-byte "image".
    assert "json" in captured
    assert "files" not in captured


def test_app_version_pyproject_fallback_reads_repo_root() -> None:
    """The pyproject.toml fallback must resolve to the real repo root — it
    regressed once by pointing one directory ABOVE it (parents[4])."""
    import jarvis.ui.web.feedback_routes as feedback_routes

    root = Path(feedback_routes.__file__).resolve().parents[3]
    assert (root / "pyproject.toml").is_file()
