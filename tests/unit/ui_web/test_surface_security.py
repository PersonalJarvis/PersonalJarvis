"""Fail-closed tests for the global HTTP/WebSocket security boundary.

These tests exercise the ASGI middleware directly.  They intentionally do not
use a dependency override or a test-only authentication bypass: every accepted
request presents the same desktop-session token, control key, or session cookie
that a production caller must present.
"""
from __future__ import annotations

from http.cookies import SimpleCookie
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.core import control_key
from jarvis.ui.web.missions_auth import register_token, reset_tokens
from jarvis.ui.web.surface_security import (
    COOKIE_NAME,
    SurfaceSecurity,
    build_set_cookie_header,
)

pytestmark = pytest.mark.no_auto_web_auth

_BASE_URL = "http://127.0.0.1:47821"
_SESSION_TOKEN = "desktop-session-token-for-surface-tests"  # noqa: S105
_CONTROL_KEY = "jctl_control_key_for_surface_tests"  # noqa: S105


class _ProbeApp:
    """Minimal ASGI app that records whether the protected app was reached."""

    def __init__(self) -> None:
        self.scopes: list[dict[str, Any]] = []

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        self.scopes.append(scope)
        if scope["type"] == "http":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"reached":true}'})
            return
        if scope["type"] == "websocket":
            await receive()
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.send", "text": '{"type":"probe"}'})
            await send({"type": "websocket.close", "code": 1000})


@pytest.fixture(autouse=True)
def _auth_state(monkeypatch: pytest.MonkeyPatch):
    reset_tokens()
    register_token(_SESSION_TOKEN)
    monkeypatch.setattr(control_key, "get_control_key", lambda: _CONTROL_KEY)
    try:
        yield
    finally:
        reset_tokens()


def _client(
    *,
    base_url: str = _BASE_URL,
    peer: tuple[str, int] = ("127.0.0.1", 50_000),
    public_urls: tuple[str, ...] = (),
) -> tuple[TestClient, _ProbeApp]:
    inner = _ProbeApp()
    secured = SurfaceSecurity(
        inner,
        vite_dev_url="http://localhost:5173",
        public_urls=public_urls,
    )
    return TestClient(secured, base_url=base_url, client=peer), inner


def _headers(
    *,
    token: str | None = None,
    origin: str | None = _BASE_URL,
    host: str = "127.0.0.1:47821",
) -> dict[str, str]:
    headers = {"Host": host}
    if origin is not None:
        headers["Origin"] = origin
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


@pytest.mark.parametrize("token", [None, "wrong-token", "", "Basic abc"])
def test_internal_http_rejects_missing_or_bad_bearer(token: str | None) -> None:
    client, inner = _client()
    headers = _headers()
    if token == "Basic abc":  # noqa: S105 - malformed synthetic auth header
        headers["Authorization"] = token
    elif token is not None:
        headers["Authorization"] = f"Bearer {token}"

    response = client.get("/api/config", headers=headers)

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"
    assert inner.scopes == []


@pytest.mark.parametrize("token", [_SESSION_TOKEN, _CONTROL_KEY])
def test_internal_http_accepts_session_token_and_control_key(token: str) -> None:
    client, inner = _client()

    response = client.get("/api/config", headers=_headers(token=token))

    assert response.status_code == 200
    assert len(inner.scopes) == 1


def test_native_bearer_client_does_not_need_an_origin_header() -> None:
    client, inner = _client()

    response = client.get(
        "/api/config",
        headers=_headers(token=_CONTROL_KEY, origin=None),
    )

    assert response.status_code == 200
    assert len(inner.scopes) == 1


def test_valid_session_cookie_authenticates_internal_http() -> None:
    client, inner = _client()
    client.cookies.set(COOKIE_NAME, _SESSION_TOKEN)

    response = client.get("/api/config", headers=_headers())

    assert response.status_code == 200
    assert len(inner.scopes) == 1


@pytest.mark.parametrize(
    "host",
    [
        "attacker.example",
        "127.0.0.1.attacker.example",
        "localhost.attacker.example",
        "127.0.0.1@attacker.example",
        "127.0.0.1,attacker.example",
        "127.0.0.1:0",
        "[::1]attacker.example",
    ],
)
def test_bad_host_is_rejected_before_valid_credentials_reach_app(host: str) -> None:
    client, inner = _client()

    response = client.get(
        "/api/config",
        headers=_headers(token=_SESSION_TOKEN, host=host),
    )

    assert response.status_code == 400
    assert inner.scopes == []


def test_duplicate_host_headers_are_rejected_as_ambiguous() -> None:
    client, inner = _client()

    response = client.get(
        "/api/config",
        headers=[
            ("Host", "127.0.0.1:47821"),
            ("Host", "attacker.example"),
            ("Origin", _BASE_URL),
            ("Authorization", f"Bearer {_CONTROL_KEY}"),
        ],
    )

    assert response.status_code == 400
    assert inner.scopes == []


def test_hostile_browser_origin_is_rejected_even_with_valid_token() -> None:
    client, inner = _client()

    response = client.get(
        "/api/config",
        headers=_headers(token=_SESSION_TOKEN, origin="https://attacker.example"),
    )

    assert response.status_code == 403
    assert inner.scopes == []


def test_duplicate_origin_headers_are_rejected_as_ambiguous() -> None:
    client, inner = _client()

    response = client.get(
        "/api/config",
        headers=[
            ("Host", "127.0.0.1:47821"),
            ("Origin", _BASE_URL),
            ("Origin", "https://attacker.example"),
            ("Authorization", f"Bearer {_CONTROL_KEY}"),
        ],
    )

    assert response.status_code == 403
    assert inner.scopes == []


def test_exact_vite_origin_is_allowed_with_valid_credentials() -> None:
    client, inner = _client()

    response = client.get(
        "/api/config",
        headers=_headers(token=_CONTROL_KEY, origin="http://localhost:5173"),
    )

    assert response.status_code == 200
    assert len(inner.scopes) == 1


def test_trusted_vite_preflight_reaches_cors_without_a_credential() -> None:
    client, inner = _client()
    headers = _headers(origin="http://localhost:5173")
    headers.update(
        {
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        }
    )

    response = client.options("/api/config", headers=headers)

    assert response.status_code == 200
    assert len(inner.scopes) == 1


def test_hostile_preflight_is_rejected_before_cors() -> None:
    client, inner = _client()
    headers = _headers(origin="https://attacker.example")
    headers["Access-Control-Request-Method"] = "POST"

    response = client.options("/api/config", headers=headers)

    assert response.status_code == 403
    assert inner.scopes == []


def test_session_cookie_builder_sets_http_only_strict_flags() -> None:
    raw_cookie = build_set_cookie_header(_SESSION_TOKEN, secure=False)
    parsed = SimpleCookie()
    parsed.load(raw_cookie)
    morsel = parsed[COOKIE_NAME]
    assert morsel.value == _SESSION_TOKEN
    assert morsel["path"] == "/"
    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "strict"
    assert not morsel["secure"]


def test_session_cookie_builder_marks_https_cookie_secure() -> None:
    parsed = SimpleCookie()
    parsed.load(build_set_cookie_header(_SESSION_TOKEN, secure=True))
    assert parsed[COOKIE_NAME]["secure"] is True


def test_session_exchange_sets_opaque_cookie_and_unlocks_next_request() -> None:
    client, inner = _client()

    response = client.post(
        "/api/ui/session",
        json={"session_token": _SESSION_TOKEN},
        headers=_headers(),
    )

    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    parsed = SimpleCookie()
    parsed.load(response.headers["set-cookie"])
    morsel = parsed[COOKIE_NAME]
    assert morsel.value not in {_SESSION_TOKEN, _CONTROL_KEY}
    assert len(morsel.value) >= 32
    assert morsel["path"] == "/"
    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "strict"
    assert not morsel["secure"]
    assert inner.scopes == []

    protected = client.get("/api/config", headers=_headers())
    assert protected.status_code == 200
    assert len(inner.scopes) == 1


def test_registered_session_token_cannot_be_replayed_after_exchange() -> None:
    client, inner = _client()

    first = client.post(
        "/api/ui/session",
        json={"session_token": _SESSION_TOKEN},
        headers=_headers(),
    )
    replay = client.post(
        "/api/ui/session",
        json={"session_token": _SESSION_TOKEN},
        headers=_headers(),
    )
    bearer_replay = client.get(
        "/api/config",
        headers=_headers(token=_SESSION_TOKEN),
    )

    assert first.status_code == 204
    assert replay.status_code == 401
    assert bearer_replay.status_code == 401
    assert inner.scopes == []


def test_bootstrap_token_is_exchange_only_not_a_general_api_credential() -> None:
    bootstrap_token = "exchange-only-bootstrap-token"  # noqa: S105
    inner = _ProbeApp()
    client = TestClient(
        SurfaceSecurity(inner, bootstrap_tokens=(bootstrap_token,)),
        base_url=_BASE_URL,
        client=("127.0.0.1", 50_000),
    )

    bearer = client.get(
        "/api/config",
        headers=_headers(token=bootstrap_token),
    )
    client.cookies.set(COOKIE_NAME, bootstrap_token)
    cookie = client.get("/api/config", headers=_headers())
    client.cookies.clear()
    exchange = client.post(
        "/api/ui/session",
        json={"session_token": bootstrap_token},
        headers=_headers(),
    )
    replay = client.post(
        "/api/ui/session",
        json={"session_token": bootstrap_token},
        headers=_headers(),
    )

    assert bearer.status_code == 401
    assert cookie.status_code == 401
    assert exchange.status_code == 204
    assert replay.status_code == 401
    assert inner.scopes == []


def test_https_control_key_exchange_marks_cookie_secure() -> None:
    public_url = "https://jarvis.example"
    client, inner = _client(
        base_url=public_url,
        peer=("203.0.113.9", 50_000),
        public_urls=(public_url,),
    )

    response = client.post(
        "/api/ui/session",
        json={"control_key": _CONTROL_KEY},
        headers=_headers(origin=public_url, host="jarvis.example"),
    )

    assert response.status_code == 204
    parsed = SimpleCookie()
    parsed.load(response.headers["set-cookie"])
    assert parsed[COOKIE_NAME]["secure"] is True
    assert inner.scopes == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"session_token": "wrong-token"},
        {"control_key": "wrong-control-key"},
        {"session_token": _SESSION_TOKEN, "control_key": _CONTROL_KEY},
    ],
)
def test_session_exchange_rejects_missing_invalid_or_ambiguous_credentials(
    payload: dict,
) -> None:
    client, inner = _client()

    response = client.post("/api/ui/session", json=payload, headers=_headers())

    expected = 400 if len(payload) != 1 else 401
    assert response.status_code == expected
    assert "set-cookie" not in response.headers
    assert inner.scopes == []


def test_non_loopback_plain_http_session_exchange_is_rejected() -> None:
    public_url = "http://jarvis.example"
    client, inner = _client(
        base_url=public_url,
        peer=("203.0.113.9", 50_000),
        public_urls=(public_url,),
    )

    response = client.post(
        "/api/ui/session",
        json={"control_key": _CONTROL_KEY},
        headers=_headers(origin=public_url, host="jarvis.example"),
    )

    assert response.status_code == 403
    assert "set-cookie" not in response.headers
    assert inner.scopes == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/marketplace/oauth/callback?code=x&state=y"),
        ("POST", "/api/telephony/voice"),
        ("POST", "/api/conductor/hooks/route-secret"),
    ],
)
def test_exact_external_callbacks_retain_their_own_auth_boundary(
    method: str,
    path: str,
) -> None:
    client, inner = _client()

    response = client.request(method, path, headers=_headers(origin=None))

    assert response.status_code == 200
    assert len(inner.scopes) == 1


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/marketplace/oauth/callback?code=x&state=y"),
        ("POST", "/api/telephony/voice"),
        ("POST", "/api/conductor/hooks/route-secret"),
    ],
)
def test_external_callbacks_reject_an_explicit_foreign_origin(
    method: str,
    path: str,
) -> None:
    client, inner = _client()

    response = client.request(
        method,
        path,
        headers=_headers(origin="https://attacker.example"),
    )

    assert response.status_code == 403
    assert inner.scopes == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/marketplace/oauth/callback?code=x&state=y"),
        ("POST", "/api/telephony/voice"),
        ("POST", "/api/conductor/hooks/route-secret"),
    ],
)
def test_external_callbacks_remain_behind_the_host_guard(
    method: str,
    path: str,
) -> None:
    client, inner = _client()

    response = client.request(
        method,
        path,
        headers=_headers(origin=None, host="attacker.example"),
    )

    assert response.status_code == 400
    assert inner.scopes == []


def test_public_telephony_websocket_allows_a_non_browser_without_origin() -> None:
    client, inner = _client()

    with client.websocket_connect(
        "/api/telephony/media",
        headers={"Host": "127.0.0.1:47821"},
    ) as websocket:
        assert websocket.receive_json() == {"type": "probe"}

    assert len(inner.scopes) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {
            "Host": "127.0.0.1:47821",
            "Origin": "https://attacker.example",
        },
        {"Host": "attacker.example"},
    ],
)
def test_public_telephony_websocket_still_enforces_host_and_supplied_origin(
    headers: dict[str, str],
) -> None:
    client, inner = _client()

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/api/telephony/media",
            headers=headers,
        ):
            pass

    assert exc_info.value.code == 4403
    assert inner.scopes == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/marketplace/oauth/callback"),
        ("GET", "/api/telephony/voice"),
        ("POST", "/api/telephony/voice/"),
        ("POST", "/api/marketplace/oauth/callback/extra"),
        ("GET", "/api/marketplace/oauth/callback/"),
        ("POST", "/api/conductor/hooks"),
        ("POST", "/api/conductor/hooks/route-secret/extra"),
    ],
)
def test_public_callback_allowlist_is_method_and_path_exact(
    method: str,
    path: str,
) -> None:
    client, inner = _client()

    response = client.request(method, path, headers=_headers(origin=None))

    assert response.status_code == 401
    assert inner.scopes == []
