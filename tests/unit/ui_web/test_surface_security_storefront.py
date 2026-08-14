"""The storefront's single cross-site door in SurfaceSecurity.

"Add Wallpaper to Personal Jarvis" on the marketplace website is a fetch from
a public https origin to this machine's loopback API — normally rejected
twice (foreign Origin, then the CSRF rule). The door admits EXACTLY the
configured storefront origin on EXACTLY the wallpaper-import path, answers
the browser's private-network preflight itself, and stamps CORS headers onto
the response. Everything else must stay as closed as before.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from jarvis.core import control_key
from jarvis.ui.web import surface_security
from jarvis.ui.web.missions_auth import register_token, reset_tokens
from jarvis.ui.web.surface_security import (
    SurfaceSecurity,
    reset_ws_tickets,
    set_browser_login_required,
)

pytestmark = pytest.mark.no_auto_web_auth

_BASE_URL = "http://127.0.0.1:47821"
_CONTROL_KEY = "jctl_control_key_for_storefront_tests"  # noqa: S105
_STOREFRONT = "https://personaljarvis.ai"
_INSTALL_PATH = "/api/marketplace/community/wallpapers/aurora-drift/install"


class _ProbeApp:
    """Minimal ASGI app that records whether the protected app was reached."""

    def __init__(self) -> None:
        self.scopes: list[dict[str, Any]] = []

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        self.scopes.append(scope)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"reached":true}'})


@pytest.fixture(autouse=True)
def _auth_state(monkeypatch: pytest.MonkeyPatch):
    reset_tokens()
    reset_ws_tickets()
    monkeypatch.setattr(control_key, "get_control_key", lambda: _CONTROL_KEY)
    previous = surface_security.browser_login_required()
    set_browser_login_required(False)
    try:
        yield
    finally:
        set_browser_login_required(previous)
        reset_tokens()
        reset_ws_tickets()


def _client(
    *, storefront: str | None = _STOREFRONT
) -> tuple[TestClient, _ProbeApp]:
    inner = _ProbeApp()
    secured = SurfaceSecurity(
        inner,
        vite_dev_url="http://localhost:5173",
        storefront_origin=storefront,
    )
    return (
        TestClient(secured, base_url=_BASE_URL, client=("127.0.0.1", 50_000)),
        inner,
    )


def _preflight_headers(origin: str = _STOREFRONT, *, private_network: bool = True):
    headers = {
        "Host": "127.0.0.1:47821",
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
    }
    if private_network:
        headers["Access-Control-Request-Private-Network"] = "true"
    return headers


def test_preflight_from_the_storefront_is_answered_with_private_network_optin() -> None:
    client, inner = _client()

    response = client.options(_INSTALL_PATH, headers=_preflight_headers())

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == _STOREFRONT
    assert "POST" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-private-network"] == "true"
    # The preflight is answered at the boundary — the app never sees it.
    assert inner.scopes == []


def test_preflight_without_the_private_network_header_gets_no_optin() -> None:
    client, _ = _client()

    response = client.options(
        _INSTALL_PATH, headers=_preflight_headers(private_network=False)
    )

    assert response.status_code == 204
    assert "access-control-allow-private-network" not in response.headers


def test_preflight_from_a_foreign_origin_is_rejected() -> None:
    client, inner = _client()

    response = client.options(
        _INSTALL_PATH, headers=_preflight_headers(origin="https://evil.example")
    )

    assert response.status_code == 403
    assert inner.scopes == []


def test_storefront_post_reaches_the_route_and_carries_cors_headers() -> None:
    client, inner = _client()

    response = client.post(
        _INSTALL_PATH, headers={"Host": "127.0.0.1:47821", "Origin": _STOREFRONT}
    )

    # Open access: loopback peer, browser lock off — same rule as the app UI.
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == _STOREFRONT
    assert len(inner.scopes) == 1


def test_a_locked_instance_answers_401_the_storefront_can_read() -> None:
    set_browser_login_required(True)
    client, inner = _client()

    response = client.post(
        _INSTALL_PATH, headers={"Host": "127.0.0.1:47821", "Origin": _STOREFRONT}
    )

    assert response.status_code == 401
    # The CORS headers are present even on the rejection, so the storefront
    # can tell "locked" apart from "not running" and say so.
    assert response.headers["access-control-allow-origin"] == _STOREFRONT
    assert inner.scopes == []


def test_a_valid_bearer_still_works_through_the_door_when_locked() -> None:
    set_browser_login_required(True)
    register_token("storefront-session-token")
    client, inner = _client()

    response = client.post(
        _INSTALL_PATH,
        headers={
            "Host": "127.0.0.1:47821",
            "Origin": _STOREFRONT,
            "Authorization": f"Bearer {_CONTROL_KEY}",
        },
    )

    assert response.status_code == 200
    assert len(inner.scopes) == 1


def test_the_storefront_origin_opens_nothing_but_the_wallpaper_path() -> None:
    client, inner = _client()

    for path in (
        "/api/config",
        "/api/marketplace/community/plugins/evil/install",
        "/api/marketplace/community/wallpapers/install",
        "/api/marketplace/community/wallpapers/a/b/install",
    ):
        response = client.post(
            path, headers={"Host": "127.0.0.1:47821", "Origin": _STOREFRONT}
        )
        assert response.status_code == 403, path
    assert inner.scopes == []


def test_only_post_and_preflight_pass_the_door() -> None:
    client, inner = _client()

    response = client.get(
        _INSTALL_PATH, headers={"Host": "127.0.0.1:47821", "Origin": _STOREFRONT}
    )

    assert response.status_code == 403
    assert inner.scopes == []


def test_an_unconfigured_or_plain_http_storefront_keeps_the_door_shut() -> None:
    for storefront in (None, "", "http://storefront.example"):
        client, inner = _client(storefront=storefront)
        response = client.post(
            _INSTALL_PATH, headers={"Host": "127.0.0.1:47821", "Origin": _STOREFRONT}
        )
        assert response.status_code == 403, repr(storefront)
        assert inner.scopes == []


def test_a_loopback_dev_storefront_may_use_plain_http() -> None:
    client, inner = _client(storefront="http://localhost:8788")

    response = client.post(
        _INSTALL_PATH,
        headers={"Host": "127.0.0.1:47821", "Origin": "http://localhost:8788"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8788"
    assert len(inner.scopes) == 1
