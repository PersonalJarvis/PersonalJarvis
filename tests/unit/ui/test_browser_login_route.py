"""GET/PUT /api/settings/browser-login — the optional browser lock.

The PUT must apply live (shared ``surface_security`` boundary flag), persist
via ``config_writer``, and — when the lock is turned ON by a caller without a
valid session — mint a fresh HttpOnly session cookie so the very browser that
enabled the lock does not lock itself out.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web import missions_auth, surface_security
from jarvis.ui.web.settings_routes import router

# The suite-wide auto-auth fixture would give every client a valid session
# cookie, hiding exactly the no-session path this file exists to prove.
pytestmark = pytest.mark.no_auto_web_auth


def _client(*, require_browser_login: bool = False) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(
        ui=SimpleNamespace(require_browser_login=require_browser_login),
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_toml_writes(monkeypatch):
    import jarvis.core.config_writer as cw

    written = {}
    monkeypatch.setattr(
        cw, "set_require_browser_login", lambda v, **k: written.setdefault("v", v)
    )
    yield written


def test_get_reports_the_config_value() -> None:
    assert _client().get("/api/settings/browser-login").json() == {"enabled": False}
    assert _client(require_browser_login=True).get("/api/settings/browser-login").json() == {
        "enabled": True
    }


def test_put_enable_without_session_mints_a_cookie(_no_toml_writes) -> None:
    missions_auth.reset_tokens()
    r = _client().put("/api/settings/browser-login", json={"enabled": True})

    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["persisted"] is True
    assert body["session_minted"] is True
    assert surface_security.browser_login_required() is True
    assert _no_toml_writes["v"] is True
    # The minted cookie is a real, registered session token.
    cookie = r.cookies.get(surface_security.COOKIE_NAME)
    assert cookie and missions_auth.validate_token(cookie)
    missions_auth.reset_tokens()


def test_put_enable_with_valid_session_mints_nothing() -> None:
    missions_auth.reset_tokens()
    token = "already-signed-in"  # noqa: S105
    missions_auth.register_token(token)
    client = _client()
    client.cookies.set(surface_security.COOKIE_NAME, token)

    r = client.put("/api/settings/browser-login", json={"enabled": True})

    assert r.status_code == 200
    assert r.json()["session_minted"] is False
    assert "set-cookie" not in r.headers
    missions_auth.reset_tokens()


def test_put_disable_applies_live_and_persists(_no_toml_writes) -> None:
    r = _client(require_browser_login=True).put(
        "/api/settings/browser-login", json={"enabled": False}
    )

    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False and body["session_minted"] is False
    assert surface_security.browser_login_required() is False
    assert _no_toml_writes["v"] is False
