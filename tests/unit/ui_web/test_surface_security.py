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
from jarvis.ui.web import surface_security
from jarvis.ui.web.missions_auth import register_token, reset_tokens
from jarvis.ui.web.surface_security import (
    COOKIE_NAME,
    SurfaceSecurity,
    build_set_cookie_header,
    credentials_valid,
    reset_ws_tickets,
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
    reset_ws_tickets()
    register_token(_SESSION_TOKEN)
    monkeypatch.setattr(control_key, "get_control_key", lambda: _CONTROL_KEY)
    try:
        yield
    finally:
        reset_tokens()
        reset_ws_tickets()


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
    # Rejects are accept-then-close so the client can read the specific code:
    # a close before the accept surfaces as an opaque 1006 in every browser,
    # which the UI cannot distinguish from "server down" (BUG-065).
    client, inner = _client()

    with client.websocket_connect(
        "/api/telephony/media",
        headers=headers,
    ) as websocket:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

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


# ---------------------------------------------------------------------------
# One-time WebSocket tickets + readable websocket rejects (BUG-065).
#
# WebKit engines (Safari / WKWebView on macOS, WebKitGTK on Linux) do not
# attach the HttpOnly session cookie to a WebSocket handshake, so cookie-only
# WS auth bricks the live event channel on every non-Chromium browser. The
# boundary therefore (a) closes rejected sockets AFTER the accept so the
# specific 4401/4403 is readable, and (b) accepts a short-lived single-use
# ticket minted over cookie-authenticated plain HTTP.
# ---------------------------------------------------------------------------


def test_cookieless_websocket_reject_is_a_readable_4401() -> None:
    client, inner = _client()

    with client.websocket_connect("/ws", headers=_headers()) as websocket:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 4401
    assert inner.scopes == []


def test_ws_ticket_mint_requires_a_credential() -> None:
    client, inner = _client()

    response = client.post("/api/ui/ws-ticket", headers=_headers())

    assert response.status_code == 401
    assert inner.scopes == []


def test_session_ws_ticket_mint_requires_an_origin() -> None:
    client, inner = _client()
    client.cookies.set(COOKIE_NAME, _SESSION_TOKEN)

    response = client.post("/api/ui/ws-ticket", headers=_headers(origin=None))

    assert response.status_code == 403
    assert inner.scopes == []


def test_ws_ticket_opens_the_socket_without_any_cookie() -> None:
    client, inner = _client()
    client.cookies.set(COOKIE_NAME, _SESSION_TOKEN)
    minted = client.post("/api/ui/ws-ticket", headers=_headers())
    assert minted.status_code == 200
    assert minted.headers["cache-control"] == "no-store"
    ticket = minted.json()["ticket"]
    assert isinstance(ticket, str) and len(ticket) >= 32

    # The WS handshake itself presents NO cookie — the WebKit reality.
    client.cookies.clear()
    with client.websocket_connect(
        f"/ws?ticket={ticket}", headers=_headers()
    ) as websocket:
        assert websocket.receive_json() == {"type": "probe"}

    assert len(inner.scopes) == 1


def test_ws_ticket_is_single_use() -> None:
    client, inner = _client()
    client.cookies.set(COOKIE_NAME, _SESSION_TOKEN)
    ticket = client.post("/api/ui/ws-ticket", headers=_headers()).json()["ticket"]
    client.cookies.clear()

    with client.websocket_connect(f"/ws?ticket={ticket}", headers=_headers()):
        pass
    with client.websocket_connect(
        f"/ws?ticket={ticket}", headers=_headers()
    ) as replay:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            replay.receive_json()

    assert exc_info.value.code == 4401
    assert len(inner.scopes) == 1


def test_expired_ws_ticket_is_rejected() -> None:
    client, inner = _client()
    client.cookies.set(COOKIE_NAME, _SESSION_TOKEN)
    ticket = client.post("/api/ui/ws-ticket", headers=_headers()).json()["ticket"]
    client.cookies.clear()
    surface_security._WS_TICKETS[ticket] = 0.0  # force expiry

    with client.websocket_connect(
        f"/ws?ticket={ticket}", headers=_headers()
    ) as websocket:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 4401
    assert inner.scopes == []


def test_ws_ticket_from_a_hostile_origin_is_rejected_before_consumption() -> None:
    client, inner = _client()
    client.cookies.set(COOKIE_NAME, _SESSION_TOKEN)
    ticket = client.post("/api/ui/ws-ticket", headers=_headers()).json()["ticket"]
    client.cookies.clear()

    with client.websocket_connect(
        f"/ws?ticket={ticket}",
        headers=_headers(origin="https://attacker.example"),
    ) as websocket:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    # The global Origin gate fires before ticket handling: the hostile page
    # is rejected outright and the unconsumed ticket stays valid for the
    # legitimate same-origin owner.
    assert exc_info.value.code == 4403
    assert inner.scopes == []
    with client.websocket_connect(
        f"/ws?ticket={ticket}", headers=_headers()
    ) as legitimate:
        assert legitimate.receive_json() == {"type": "probe"}
    assert len(inner.scopes) == 1


def test_ws_ticket_without_any_origin_is_consumed_and_rejected() -> None:
    # required=True on the ticket path: a browser always sends an Origin on a
    # WS handshake, so an origin-less ticket presentation is not a browser —
    # native clients authenticate with a Bearer instead. Fail-closed AND burn
    # the ticket so it cannot be probed origin-less and replayed later.
    client, inner = _client()
    client.cookies.set(COOKIE_NAME, _SESSION_TOKEN)
    ticket = client.post("/api/ui/ws-ticket", headers=_headers()).json()["ticket"]
    client.cookies.clear()

    with client.websocket_connect(
        f"/ws?ticket={ticket}", headers=_headers(origin=None)
    ) as websocket:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()
    assert exc_info.value.code == 4403

    with client.websocket_connect(
        f"/ws?ticket={ticket}", headers=_headers()
    ) as replay:
        with pytest.raises(WebSocketDisconnect) as replay_exc:
            replay.receive_json()
    assert replay_exc.value.code == 4401
    assert inner.scopes == []


def test_ws_ticket_never_authenticates_plain_http() -> None:
    client, inner = _client()
    client.cookies.set(COOKIE_NAME, _SESSION_TOKEN)
    ticket = client.post("/api/ui/ws-ticket", headers=_headers()).json()["ticket"]
    client.cookies.clear()

    response = client.get(f"/api/config?ticket={ticket}", headers=_headers())

    assert response.status_code == 401
    assert inner.scopes == []


def test_ticket_authenticated_socket_passes_route_level_recheck() -> None:
    """Tool-capable sockets (/ws/audio, workspace PTY) re-check credentials
    route-locally via ``credentials_valid``. A ticket is consumed by the outer
    boundary and cannot be validated twice, so the boundary stamps the scope
    and the re-check must honor it — otherwise the exact WebKit clients the
    ticket exists for (BUG-065) get accepted by the boundary and then closed
    4401 by the route itself."""
    client, inner = _client()
    client.cookies.set(COOKIE_NAME, _SESSION_TOKEN)
    ticket = client.post("/api/ui/ws-ticket", headers=_headers()).json()["ticket"]
    client.cookies.clear()

    with client.websocket_connect(
        f"/ws/audio?ticket={ticket}", headers=_headers()
    ) as websocket:
        assert websocket.receive_json() == {"type": "probe"}

    assert len(inner.scopes) == 1
    assert credentials_valid(inner.scopes[0]) is True


def test_non_loopback_plain_http_ws_ticket_mint_is_rejected() -> None:
    public_url = "http://jarvis.example"
    client, inner = _client(
        base_url=public_url,
        peer=("203.0.113.9", 50_000),
        public_urls=(public_url,),
    )
    client.cookies.set(COOKIE_NAME, _SESSION_TOKEN)

    response = client.post(
        "/api/ui/ws-ticket",
        headers=_headers(origin=public_url, host="jarvis.example"),
    )

    assert response.status_code == 403
    assert inner.scopes == []


def test_non_loopback_plain_ws_ticket_presentation_is_rejected() -> None:
    # Mint legitimately over loopback, then present the ticket over a
    # sniffable plain-HTTP non-loopback transport: refused, unconsumed.
    loopback_client, _ = _client()
    loopback_client.cookies.set(COOKIE_NAME, _SESSION_TOKEN)
    ticket = loopback_client.post(
        "/api/ui/ws-ticket", headers=_headers()
    ).json()["ticket"]

    public_url = "http://jarvis.example"
    client, inner = _client(
        base_url=public_url,
        peer=("203.0.113.9", 50_000),
        public_urls=(public_url,),
    )
    with client.websocket_connect(
        f"/ws?ticket={ticket}",
        headers=_headers(origin=public_url, host="jarvis.example"),
    ) as websocket:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 4403
    assert inner.scopes == []


# ---------------------------------------------------------------------------
# Optional browser lock — OFF (the product default): loopback-to-loopback
# requests are authorized without any credential; everything non-loopback
# keeps demanding the Control Key. The suite-wide conftest fixture pins the
# lock ON, so these tests flip it OFF explicitly and rely on its reset.
# ---------------------------------------------------------------------------


def test_browser_lock_off_grants_loopback_http_without_credential() -> None:
    surface_security.set_browser_login_required(False)
    client, inner = _client()

    response = client.get("/api/config", headers=_headers())

    assert response.status_code == 200
    assert len(inner.scopes) == 1


def test_browser_lock_off_ignores_invalid_credentials_on_loopback() -> None:
    # Open access trusts the machine's user by decision, not by token: a
    # stale key in a local tool must not lock the local user out.
    surface_security.set_browser_login_required(False)
    client, inner = _client()

    response = client.get(
        "/api/config", headers=_headers(token="jctl_stale")  # noqa: S106 — synthetic stale key
    )

    assert response.status_code == 200
    assert len(inner.scopes) == 1


def test_browser_lock_off_unsafe_method_keeps_csrf_origin_gate() -> None:
    surface_security.set_browser_login_required(False)
    client, inner = _client()

    origin_less = client.post("/api/chat", headers=_headers(origin=None), json={})
    assert origin_less.status_code == 403
    assert inner.scopes == []

    same_origin = client.post("/api/chat", headers=_headers(), json={})
    assert same_origin.status_code == 200
    assert len(inner.scopes) == 1


def test_browser_lock_off_never_opens_non_loopback() -> None:
    surface_security.set_browser_login_required(False)
    public_url = "http://jarvis.example"
    client, inner = _client(
        base_url=public_url,
        peer=("203.0.113.9", 50_000),
        public_urls=(public_url,),
    )

    response = client.get(
        "/api/config", headers=_headers(origin=public_url, host="jarvis.example")
    )

    assert response.status_code == 401
    assert inner.scopes == []


@pytest.mark.parametrize(
    "relay_header",
    [
        ("X-Forwarded-For", "203.0.113.9"),
        ("Forwarded", "for=203.0.113.9"),
        ("Via", "1.1 relay"),
        ("X-Real-IP", "203.0.113.9"),
    ],
)
def test_browser_lock_off_relayed_loopback_is_rejected(
    relay_header: tuple[str, str],
) -> None:
    # A tunnel/proxy on the same machine connects from 127.0.0.1 and lets the
    # remote caller choose the Host header (e.g. "127.0.0.1"). Any forwarding
    # indicator proves the peer socket belongs to a relay, so open access must
    # refuse and demand the key.
    surface_security.set_browser_login_required(False)
    client, inner = _client()

    name, value = relay_header
    headers = _headers()
    headers[name] = value
    response = client.get("/api/config", headers=headers)

    assert response.status_code == 401
    assert inner.scopes == []


def test_browser_lock_off_reverse_proxy_public_host_stays_locked() -> None:
    # A same-host reverse proxy connects from 127.0.0.1 but forwards a public
    # Host header — both ends must be loopback for open access to grant.
    surface_security.set_browser_login_required(False)
    public_url = "http://jarvis.example"
    client, inner = _client(
        base_url=public_url,
        peer=("127.0.0.1", 50_000),
        public_urls=(public_url,),
    )

    response = client.get(
        "/api/config", headers=_headers(origin=public_url, host="jarvis.example")
    )

    assert response.status_code == 401
    assert inner.scopes == []


def test_browser_lock_off_grants_loopback_websocket_with_origin() -> None:
    surface_security.set_browser_login_required(False)
    client, inner = _client()

    with client.websocket_connect("/ws", headers=_headers()) as websocket:
        assert websocket.receive_json() == {"type": "probe"}

    assert len(inner.scopes) == 1
    # Route-level defense-in-depth re-checks must agree with the boundary.
    assert credentials_valid(inner.scopes[0]) is True


def test_browser_lock_off_websocket_without_origin_is_rejected() -> None:
    # A browser always attaches an Origin to a WS handshake; an origin-less
    # socket is not a browser and must present a real credential instead.
    surface_security.set_browser_login_required(False)
    client, inner = _client()

    with client.websocket_connect(
        "/ws", headers=_headers(origin=None)
    ) as websocket:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 4403
    assert inner.scopes == []


def test_browser_lock_on_keeps_loopback_locked() -> None:
    surface_security.set_browser_login_required(True)
    client, inner = _client()

    response = client.get("/api/config", headers=_headers())

    assert response.status_code == 401
    assert inner.scopes == []


# ---------------------------------------------------------------------------
# Lazy raw-TOML seed: before any explicit seed, the boundary derives the flag
# from ``[ui].require_browser_login`` without importing the config graph.
# ---------------------------------------------------------------------------


def _seed_from_toml(tmp_path, monkeypatch, content: str | None) -> bool:
    if content is None:
        monkeypatch.setenv("JARVIS_CONFIG", str(tmp_path / "missing.toml"))
    else:
        config_file = tmp_path / "jarvis.toml"
        config_file.write_bytes(content.encode("utf-8"))
        monkeypatch.setenv("JARVIS_CONFIG", str(config_file))
    surface_security.reset_browser_login_required()
    return surface_security.browser_login_required()


def test_toml_seed_field_true_requires_login(tmp_path, monkeypatch) -> None:
    assert _seed_from_toml(
        tmp_path, monkeypatch, "[ui]\nrequire_browser_login = true\n"
    ) is True


def test_toml_seed_field_absent_defaults_open(tmp_path, monkeypatch) -> None:
    assert _seed_from_toml(tmp_path, monkeypatch, "[ui]\ndev_mode = false\n") is False


def test_toml_seed_missing_file_defaults_open(tmp_path, monkeypatch) -> None:
    assert _seed_from_toml(tmp_path, monkeypatch, None) is False


def test_toml_seed_unreadable_file_fails_closed(tmp_path, monkeypatch) -> None:
    assert _seed_from_toml(tmp_path, monkeypatch, "not [valid toml ==") is True


def test_toml_seed_tolerates_utf8_bom(tmp_path, monkeypatch) -> None:
    config_file = tmp_path / "jarvis.toml"
    config_file.write_bytes(b"\xef\xbb\xbf[ui]\nrequire_browser_login = true\n")
    monkeypatch.setenv("JARVIS_CONFIG", str(config_file))
    surface_security.reset_browser_login_required()
    assert surface_security.browser_login_required() is True
