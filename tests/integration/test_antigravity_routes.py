"""Routes for the Antigravity (Google-subscription) provider connect flow."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.interactive_terminal import InteractiveTerminalUnavailable
from jarvis.google_cli.auth_service import GoogleCliAuthStatus
from jarvis.ui.web import antigravity_routes


def _client(monkeypatch, *, status: GoogleCliAuthStatus, login_raises=None):
    class _FakeService:
        def status(self):
            return status

        def start_login(self):
            if login_raises is not None:
                raise login_raises

            class _P:
                pid = 999

            return _P()

        def logout_blocking(self):
            return True, None

    monkeypatch.setattr(
        antigravity_routes, "GoogleCliAuthService", lambda *a, **k: _FakeService()
    )
    app = FastAPI()
    app.include_router(antigravity_routes.router)
    return TestClient(app)


def test_status_connected(monkeypatch):
    st = GoogleCliAuthStatus(
        installed=True, connected=True, mode="oauth-personal",
        cli_kind="gemini", user_email="user@example.com", message="ok",
    )
    client = _client(monkeypatch, status=st)
    resp = client.get("/api/antigravity/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["installed"] is True
    assert body["connected"] is True
    assert body["mode"] == "oauth-personal"
    assert body["cli_kind"] == "gemini"


def test_login_409_when_not_installed(monkeypatch):
    st = GoogleCliAuthStatus(installed=False, connected=False)
    client = _client(monkeypatch, status=st)
    resp = client.post("/api/antigravity/login")
    assert resp.status_code == 409


def test_login_ok_when_installed(monkeypatch):
    st = GoogleCliAuthStatus(installed=True, connected=False)
    client = _client(monkeypatch, status=st)
    resp = client.post("/api/antigravity/login")
    assert resp.status_code == 200
    assert resp.json()["pid"] == 999


def test_login_409_when_no_graphical_terminal(monkeypatch):
    st = GoogleCliAuthStatus(installed=True, connected=False)
    client = _client(
        monkeypatch,
        status=st,
        login_raises=InteractiveTerminalUnavailable(
            "No graphical terminal. Open a terminal and run: agy"
        ),
    )
    resp = client.post("/api/antigravity/login")
    assert resp.status_code == 409
    assert "run: agy" in resp.json()["detail"]


def test_logout_ok(monkeypatch):
    st = GoogleCliAuthStatus(installed=True, connected=True)
    client = _client(monkeypatch, status=st)
    resp = client.post("/api/antigravity/logout")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
