"""The outer boundary refuses browser requests another site started.

Browser-shaped credentials (the session cookie and loopback open access) ride
every request a browser makes to the app's address, including the ones that
carry no ``Origin``: an ``<img>``, a link, a ``no-cors`` fetch. ``Sec-Fetch-Site``
is the only signal that tells them apart from the app's own requests. Bearer
callers are not browsers and stay unaffected.

Also pins the onboarding exemption to reads: the writes record the user's
consent to the Terms and need the same credential as any other change.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from jarvis.core import control_key
from jarvis.ui.web import surface_security
from jarvis.ui.web.missions_auth import register_token, reset_tokens
from jarvis.ui.web.surface_security import COOKIE_NAME, SurfaceSecurity, foreign_site_initiated

pytestmark = pytest.mark.no_auto_web_auth

_BASE_URL = "http://127.0.0.1:47821"
_SESSION_TOKEN = "session-token-for-cross-site-tests"  # noqa: S105
_CONTROL_KEY = "jctl_control_key_for_cross_site_tests"  # noqa: S105


class _ProbeApp:
    def __init__(self) -> None:
        self.paths: list[str] = []

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        self.paths.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"reached"})


@pytest.fixture(autouse=True)
def _auth_state(monkeypatch: pytest.MonkeyPatch):
    reset_tokens()
    register_token(_SESSION_TOKEN)
    monkeypatch.setattr(control_key, "get_control_key", lambda: _CONTROL_KEY)
    try:
        yield
    finally:
        reset_tokens()


def _client(peer: tuple[str, int] = ("127.0.0.1", 50_000)) -> tuple[TestClient, _ProbeApp]:
    inner = _ProbeApp()
    secured = SurfaceSecurity(inner, vite_dev_url="http://localhost:5173")
    return TestClient(secured, base_url=_BASE_URL, client=peer), inner


def _browser(site: str | None, *, origin: str | None = None, cookie: bool = False) -> dict:
    headers: dict[str, Any] = {"Host": "127.0.0.1:47821"}
    if site is not None:
        headers["Sec-Fetch-Site"] = site
    if origin is not None:
        headers["Origin"] = origin
    if cookie:
        headers["Cookie"] = f"{COOKIE_NAME}={_SESSION_TOKEN}"
    return headers


@pytest.mark.parametrize("site", ["cross-site", "same-site", "CROSS-SITE"])
def test_open_access_refuses_a_get_another_site_started(site: str) -> None:
    surface_security.set_browser_login_required(False)
    client, inner = _client()

    response = client.get("/api/control/api-key", headers=_browser(site))

    assert response.status_code == 403
    assert inner.paths == []


@pytest.mark.parametrize("site", [None, "same-origin", "none"])
def test_open_access_still_serves_own_typed_and_non_browser_gets(site: str | None) -> None:
    surface_security.set_browser_login_required(False)
    client, inner = _client()

    response = client.get("/api/settings", headers=_browser(site))

    assert response.status_code == 200
    assert inner.paths == ["/api/settings"]


def test_session_cookie_refuses_a_cross_site_get() -> None:
    client, inner = _client()

    response = client.get("/api/settings", headers=_browser("cross-site", cookie=True))

    assert response.status_code == 403
    assert inner.paths == []


def test_session_cookie_same_origin_get_still_passes() -> None:
    client, inner = _client()

    response = client.get("/api/settings", headers=_browser("same-origin", cookie=True))

    assert response.status_code == 200


def test_trusted_dev_origin_may_call_cross_site() -> None:
    client, inner = _client()

    response = client.get(
        "/api/settings",
        headers=_browser("cross-site", origin="http://localhost:5173", cookie=True),
    )

    assert response.status_code == 200


def test_bearer_control_key_is_not_a_browser_credential() -> None:
    client, inner = _client()
    headers = _browser("cross-site")
    headers["Authorization"] = f"Bearer {_CONTROL_KEY}"

    response = client.get("/api/settings", headers=headers)

    assert response.status_code == 200


def test_cross_site_navigation_to_the_static_shell_still_works() -> None:
    surface_security.set_browser_login_required(False)
    client, inner = _client()

    response = client.get("/", headers=_browser("cross-site"))

    assert response.status_code == 200


def test_foreign_site_initiated_reads_only_the_fetch_metadata_header() -> None:
    def scope(*values: str) -> dict[str, Any]:
        return {"headers": [(b"sec-fetch-site", v.encode("latin-1")) for v in values]}

    assert foreign_site_initiated(scope("cross-site"))
    assert foreign_site_initiated(scope("same-site"))
    assert foreign_site_initiated(scope("same-origin", "cross-site"))
    assert not foreign_site_initiated(scope("same-origin"))
    assert not foreign_site_initiated(scope("none"))
    assert not foreign_site_initiated({"headers": []})


# ---------------------------------------------------------------------------
# Onboarding: reads public, consent writes credentialed
# ---------------------------------------------------------------------------


def test_onboarding_state_stays_public_before_any_credential() -> None:
    client, inner = _client()

    response = client.get("/api/onboarding/state", headers=_browser(None))

    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/api/onboarding/accept-terms", "/api/onboarding/complete"])
def test_onboarding_writes_need_a_credential(path: str) -> None:
    client, inner = _client()

    response = client.post(path, headers=_browser(None))

    assert response.status_code == 401
    assert inner.paths == []


def test_remote_caller_cannot_accept_the_terms_without_a_credential() -> None:
    surface_security.set_browser_login_required(False)
    client, inner = _client(peer=("203.0.113.7", 40_000))

    response = client.post(
        "/api/onboarding/accept-terms",
        headers={"Host": "127.0.0.1:47821", "Origin": _BASE_URL},
    )

    assert response.status_code == 401
    assert inner.paths == []


def test_local_user_page_can_still_accept_the_terms() -> None:
    surface_security.set_browser_login_required(False)
    client, inner = _client()

    response = client.post(
        "/api/onboarding/accept-terms",
        headers=_browser("same-origin", origin=_BASE_URL),
    )

    assert response.status_code == 200
    assert inner.paths == ["/api/onboarding/accept-terms"]
