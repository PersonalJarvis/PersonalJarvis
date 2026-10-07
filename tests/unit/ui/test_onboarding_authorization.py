"""Production setup boundaries preserve public reads without public writes."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.setup import onboarding_fastpath, state
from jarvis.ui.web import onboarding_routes, surface_security
from jarvis.ui.web.fast_bootstrap import FastBootstrap

pytestmark = pytest.mark.no_auto_web_auth


@pytest.mark.parametrize("desktop", [False, True])
def test_locked_completion_requires_session_and_restarts_only_once(tmp_path, monkeypatch, desktop):
    path = tmp_path / "state.json"
    monkeypatch.setattr(onboarding_routes, "_STATE_PATH_OVERRIDE", path)
    monkeypatch.setattr(surface_security, "browser_login_required", lambda: True)
    monkeypatch.delenv("JARVIS_FORCE_ONBOARDING", raising=False)
    restarts = []
    app = FastAPI()
    app.include_router(onboarding_routes.router)
    if desktop:
        app.state.desktop_app = SimpleNamespace(request_restart=lambda: restarts.append(1) or True)
    secured = surface_security.SurfaceSecurity(
        app,
        session_validator=lambda value: value == "fixture-session",
        control_key_validator=lambda value: False,
    )
    with TestClient(secured, base_url="http://127.0.0.1", client=("198.51.100.25", 1234)) as client:
        assert client.get("/api/onboarding/state").status_code == 200
        for route in ("complete", "accept-terms", "acknowledge-wake-word", "decline-terms"):
            assert (
                client.post(
                    f"/api/onboarding/{route}", headers={"Origin": "http://127.0.0.1"}
                ).status_code
                == 401
            )
        assert restarts == [] and not state.is_onboarding_complete(path)
        headers = {"Origin": "http://127.0.0.1", "Cookie": "jarvis_session=fixture-session"}
        first = client.post("/api/onboarding/complete", headers=headers)
        second = client.post("/api/onboarding/complete", headers=headers)
        assert first.json() == {"ok": True, "restarting": desktop}
        assert second.json() == {"ok": True, "restarting": False}
        assert (
            client.post(
                "/api/onboarding/complete", headers={"Origin": "http://127.0.0.1"}
            ).status_code
            == 401
        )
    assert state.is_onboarding_complete(path)
    assert restarts == ([1] if desktop else [])


@pytest.mark.parametrize(
    "headers",
    [
        [(b"host", b"127.0.0.1"), (b"origin", b"https://foreign.invalid")],
        [(b"host", b"127.0.0.1"), (b"origin", b"null")],
        [
            (b"host", b"127.0.0.1"),
            (b"origin", b"http://127.0.0.1"),
            (b"origin", b"http://127.0.0.1"),
        ],
        [(b"host", b"untrusted.invalid"), (b"origin", b"http://untrusted.invalid")],
        [(b"host", b"127.0.0.1")],
    ],
)
async def test_actual_listener_callable_guards_warming_setup(tmp_path, monkeypatch, headers):
    import uvicorn

    path = tmp_path / "state.json"
    monkeypatch.setattr(onboarding_fastpath, "_STATE_PATH_OVERRIDE", path)
    monkeypatch.setattr(surface_security, "browser_login_required", lambda: False)

    class Server:
        def __init__(self, config):
            self.config = config
            self.started = True

        async def serve(self):
            return None

    monkeypatch.setattr(uvicorn, "Server", Server)
    boot = FastBootstrap(dist_dir=tmp_path)
    await boot._start_server("127.0.0.1", 47111)
    await boot._task
    entry = boot._server.config.app

    async def drive(request_headers):
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await entry(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/onboarding/complete",
                "scheme": "http",
                "headers": request_headers,
                "client": ("127.0.0.1", 1234),
                "server": ("127.0.0.1", 80),
            },
            receive,
            send,
        )
        return messages[0]["status"]

    assert await drive(headers) in (400, 403)
    assert not state.is_onboarding_complete(path)
    assert await drive([(b"host", b"127.0.0.1"), (b"origin", b"http://127.0.0.1")]) == 200
    assert state.is_onboarding_complete(path)
