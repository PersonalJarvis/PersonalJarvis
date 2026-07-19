"""Routes for the Claude subscription CLI connection flow."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.claude_auth import ClaudeAuthStatus
from jarvis.core.interactive_terminal import InteractiveTerminalUnavailable
from jarvis.ui.web import claude_routes


def _client(monkeypatch, *, status: ClaudeAuthStatus, login_raises=None):
    class _FakeService:
        def status(self):
            return status

        def start_login(self):
            if login_raises is not None:
                raise login_raises
            return type("Launch", (), {"pid": 321})()

        def logout_blocking(self):
            return True, None

    monkeypatch.setattr(claude_routes, "_service", lambda: _FakeService())
    app = FastAPI()
    app.include_router(claude_routes.router)
    return TestClient(app)


def test_status_surfaces_cli_snapshot(monkeypatch):
    client = _client(
        monkeypatch,
        status=ClaudeAuthStatus(
            installed=True,
            connected=True,
            mode="subscription",
            account_label="Claude Max",
        ),
    )
    response = client.get("/api/claude/status")
    assert response.status_code == 200
    assert response.json()["mode"] == "subscription"


def test_login_rejects_missing_cli_with_install_command(monkeypatch):
    client = _client(
        monkeypatch,
        status=ClaudeAuthStatus(installed=False, connected=False),
    )
    response = client.post("/api/claude/login")
    assert response.status_code == 409
    assert response.json()["detail"]["install_command"]


def test_login_returns_after_visible_terminal_launch(monkeypatch):
    client = _client(
        monkeypatch,
        status=ClaudeAuthStatus(installed=True, connected=False),
    )
    response = client.post("/api/claude/login")
    assert response.status_code == 200
    assert response.json()["pid"] == 321


def test_login_headless_failure_is_an_honest_conflict(monkeypatch):
    client = _client(
        monkeypatch,
        status=ClaudeAuthStatus(installed=True, connected=False),
        login_raises=InteractiveTerminalUnavailable(
            "No graphical terminal. Open a terminal and run: claude auth login --claudeai"
        ),
    )
    response = client.post("/api/claude/login")
    assert response.status_code == 409
    assert "claude auth login" in response.json()["detail"]
