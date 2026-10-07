"""Explicit browser restart is authenticated, discoverable, and fails visibly."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.society.browser import recovery
from jarvis.ui.web.society_browser_routes import router
from jarvis.ui.web.surface_security import COOKIE_NAME, SurfaceSecurity
from tests.fakes.fake_browser_profiles import ProfileRoster, already_started

pytestmark = pytest.mark.no_auto_web_auth


@pytest.fixture
def api(monkeypatch):
    calls = []
    app = FastAPI()
    app.include_router(router)
    live = object()
    app.state.society = SimpleNamespace(
        roster=ProfileRoster(), browser=SimpleNamespace(live=live), ensure_started=already_started,
    )
    async def restart(manager, agent):
        assert manager is live
        calls.append(agent.agent_id)

    monkeypatch.setattr(recovery, "restart_browser", restart)
    client = TestClient(
        SurfaceSecurity(app, session_validator=lambda value: value == "recovery-fixture"),
        base_url="http://127.0.0.1:8765", client=("127.0.0.1", 40000),
        headers={"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"},
    )
    client.cookies.set(COOKIE_NAME, "recovery-fixture")
    return app, client, calls


def test_restart_endpoint_and_cli_metadata(api):
    app, client, calls = api
    path = "/api/society/agents/{agent_id}/browser/restart"
    operation = app.openapi()["paths"][path]["post"]
    assert operation["x-jarvis-dangerous"] is True
    assert operation["tags"] == ["society"]
    assert operation["operationId"].startswith("restart_agent_browser")
    assert client.post(path.replace("{agent_id}", "lead")).json() == {"restarted": True}
    assert calls == ["lead"]


def test_missing_agent_does_not_restart(api):
    _app, client, calls = api
    assert client.post("/api/society/agents/missing/browser/restart").status_code == 404
    assert calls == []


@pytest.mark.parametrize("error,status", [(ValueError("External browser"), 409),
                                         (RuntimeError("private diagnostics"), 503)])
def test_failure_is_not_reported_as_success(api, monkeypatch, error, status):
    _app, client, _calls = api
    async def fail(*args):
        raise error

    monkeypatch.setattr(recovery, "restart_browser", fail)
    response = client.post("/api/society/agents/lead/browser/restart")
    assert response.status_code == status
    assert "private diagnostics" not in response.text


def test_cross_site_page_cannot_restart_a_browser(api):
    _app, client, calls = api
    response = client.post("/api/society/agents/lead/browser/restart", headers={
        "Origin": "https://unrelated.example", "Sec-Fetch-Site": "cross-site",
    })
    assert response.status_code == 403
    assert calls == []


def test_missing_credential_cannot_restart_a_browser(api):
    _app, client, calls = api
    client.cookies.clear()
    assert client.post("/api/society/agents/lead/browser/restart").status_code == 401
    assert calls == []
