"""Production-assembly regressions for the Personal Jarvis web boundary."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.core import control_key
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.missions_auth import reset_tokens
from jarvis.ui.web.server import WebServer
from jarvis.ui.web.surface_security import COOKIE_NAME

pytestmark = pytest.mark.no_auto_web_auth

_BASE_URL = "http://127.0.0.1:47821"
_SESSION_TOKEN = "desktop-session-token-for-webserver-tests"  # noqa: S105
_CONTROL_KEY = "jctl_control_key_for_webserver_tests"  # noqa: S105
_TRUSTED_HEADERS = {
    "Host": "127.0.0.1:47821",
    "Origin": _BASE_URL,
}


@pytest.fixture
def web_server(monkeypatch: pytest.MonkeyPatch) -> WebServer:
    reset_tokens()
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    cfg.ui.vite_dev_url = "http://localhost:5173"
    # These tests assert the LOCKED boundary; the product default is open on
    # loopback, which would let the credential-free requests below through.
    cfg.ui.require_browser_login = True
    monkeypatch.setenv(cfg.ui.auth_token_env, _SESSION_TOKEN)
    monkeypatch.setattr(control_key, "get_control_key", lambda: _CONTROL_KEY)
    server = WebServer(cfg, bus=EventBus())
    try:
        yield server
    finally:
        reset_tokens()


def _client(server: WebServer) -> TestClient:
    return TestClient(
        server.app,
        base_url=_BASE_URL,
        client=("127.0.0.1", 50_000),
    )


def test_production_server_does_not_trust_the_vite_development_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_tokens()
    cfg = JarvisConfig()
    cfg.ui.dev_mode = False
    cfg.ui.vite_dev_url = "http://localhost:5173"
    cfg.ui.require_browser_login = True
    monkeypatch.setattr(control_key, "get_control_key", lambda: _CONTROL_KEY)
    server = WebServer(cfg, bus=EventBus())

    response = _client(server).get(
        "/api/config",
        headers={
            "Host": "127.0.0.1:47821",
            "Origin": cfg.ui.vite_dev_url,
            "Authorization": f"Bearer {_CONTROL_KEY}",
        },
    )

    assert response.status_code == 403


def test_main_websocket_rejects_unauthenticated_terminal_before_dispatch(
    web_server: WebServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn = AsyncMock()
    monkeypatch.setattr(web_server, "_handle_terminal_spawn", spawn)

    with _client(web_server) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws", headers=_TRUSTED_HEADERS) as ws:
                ws.send_json(
                    {
                        "type": "command",
                        "action": "terminal.spawn",
                        "payload": {"shell": "pwsh"},
                    }
                )
                ws.receive_json()

    assert exc_info.value.code == 4401
    spawn.assert_not_awaited()
    assert web_server._clients == {}  # noqa: SLF001


def test_main_websocket_welcome_never_contains_a_credential(
    web_server: WebServer,
) -> None:
    with _client(web_server) as client:
        exchange = client.post(
            "/api/ui/session",
            json={"control_key": _CONTROL_KEY},
            headers=_TRUSTED_HEADERS,
        )
        assert exchange.status_code == 204
        cookie = exchange.cookies.get(COOKIE_NAME)
        assert cookie is not None
        with client.websocket_connect(
            "/ws",
            headers={**_TRUSTED_HEADERS, "Cookie": f"{COOKIE_NAME}={cookie}"},
        ) as ws:
            welcome = ws.receive_json()

    assert welcome["type"] == "welcome"
    assert "token" not in welcome
    assert _SESSION_TOKEN not in json.dumps(welcome)
    assert _CONTROL_KEY not in json.dumps(welcome)


def test_session_exchange_sets_cookie_and_unlocks_protected_rest(
    web_server: WebServer,
) -> None:
    client = _client(web_server)

    exchange = client.post(
        "/api/ui/session",
        json={"control_key": _CONTROL_KEY},
        headers=_TRUSTED_HEADERS,
    )

    assert exchange.status_code == 204
    assert exchange.headers["cache-control"] == "no-store"
    cookie = exchange.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie
    assert _SESSION_TOKEN not in cookie
    assert _CONTROL_KEY not in cookie
    protected = client.get("/api/config", headers=_TRUSTED_HEADERS)
    assert protected.status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"session_token": _SESSION_TOKEN},
        {"session_token": "wrong-token"},
        {"control_key": "wrong-control-key"},
    ],
)
def test_session_exchange_rejects_missing_or_invalid_credentials(
    web_server: WebServer,
    payload: dict,
) -> None:
    response = _client(web_server).post(
        "/api/ui/session",
        json=payload,
        headers=_TRUSTED_HEADERS,
    )

    expected = 400 if not payload else 401
    assert response.status_code == expected
    assert "set-cookie" not in response.headers


def test_unauthenticated_raw_mcp_config_is_rejected_before_write(
    web_server: WebServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.mcp import state as mcp_state

    save_config = Mock()
    monkeypatch.setattr(mcp_state, "save_config", save_config)

    response = _client(web_server).put(
        "/api/mcps/config/raw",
        json={"mcpServers": {"hostile": {"command": "powershell"}}},
        headers=_TRUSTED_HEADERS,
    )

    assert response.status_code == 401
    save_config.assert_not_called()


def test_unauthenticated_mcp_start_is_rejected_before_process_start(
    web_server: WebServer,
) -> None:
    start_enabled = AsyncMock()
    web_server.app.state.mcp_registry = SimpleNamespace(
        get_spec=lambda _name: object(),
        active_clients=lambda: {},
        start_enabled=start_enabled,
    )

    response = _client(web_server).post(
        "/api/mcps/hostile/start",
        headers=_TRUSTED_HEADERS,
    )

    assert response.status_code == 401
    start_enabled.assert_not_awaited()


def test_unauthenticated_custom_cli_is_rejected_before_registration(
    web_server: WebServer,
) -> None:
    catalog = Mock()
    catalog.get.return_value = None
    registry = Mock()
    registry.catalog.return_value = catalog
    registry.refresh_status = AsyncMock()
    web_server.app.state.cli_registry = registry

    response = _client(web_server).post(
        "/api/clis/custom",
        json={
            "name": "hostile-cli",
            "display_name": "Hostile CLI",
            "binary_name": "powershell",
            "check_command": ["powershell", "-Command", "Write-Output pwned"],
        },
        headers=_TRUSTED_HEADERS,
    )

    assert response.status_code == 401
    catalog.register_custom.assert_not_called()
    registry.refresh_status.assert_not_awaited()
