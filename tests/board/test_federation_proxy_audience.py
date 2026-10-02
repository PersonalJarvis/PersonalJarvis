"""The board federation proxy signs every request for ONE backend endpoint.

The backend refuses a signature whose ``aud`` does not name the route it
arrives at (``board_backend.auth._check_audience``); these tests drive the
proxy routes against the real backend app over ``httpx.ASGITransport``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip(
    "board_backend",
    reason="board-backend module not on sys.path (public snapshot / plain checkout)",
)
from board_backend.config import Settings  # noqa: E402
from board_backend.crypto import generate_keypair  # noqa: E402
from board_backend.main import create_app  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from jarvis.ui.web import federation_proxy_routes as proxy  # noqa: E402

_BACKEND = "http://backend"
_ADMIN = "test-admin"  # noqa: S105 - fixture value for a throwaway in-memory backend
# The proxy builds its client from the shared ``httpx`` module, so the tests
# patch that attribute; their own client must be the real class.
_RealAsyncClient = httpx.AsyncClient


@pytest.fixture
def keypair() -> tuple[str, str]:
    return generate_keypair()


@pytest.fixture
def backend(tmp_path: Path, keypair: tuple[str, str]) -> FastAPI:
    app = create_app(
        settings=Settings(
            admin_token=_ADMIN,
            db_path=tmp_path / "backend.db",
            register_rate_limit_per_minute=100,
            replay_window_seconds=300,
        )
    )
    app.state.disable_background = True
    return app


def _proxy_app(monkeypatch: pytest.MonkeyPatch, keypair: tuple[str, str],
               transport: httpx.AsyncBaseTransport) -> FastAPI:
    def _client(*_args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=transport, timeout=kwargs.get("timeout", 10.0))

    monkeypatch.setattr(proxy.httpx, "AsyncClient", _client)
    monkeypatch.setattr(proxy, "_KeyringBackend", lambda: None)
    monkeypatch.setattr(proxy, "_load_or_create_privkey", lambda _kr: keypair)
    app = FastAPI()
    app.include_router(proxy.router)
    app.state.config = SimpleNamespace(
        board=SimpleNamespace(federation=SimpleNamespace(backend_url=_BACKEND))
    )
    return app


async def _call(app: FastAPI, method: str, url: str, **kwargs: Any) -> httpx.Response:
    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://jarvis"
    ) as client:
        return await client.request(method, url, **kwargs)


@pytest.mark.asyncio
async def test_proxied_get_is_accepted_by_the_backend(
    monkeypatch: pytest.MonkeyPatch, backend: FastAPI, keypair: tuple[str, str]
) -> None:
    _, pub = keypair
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=backend), base_url=_BACKEND
    ) as admin:
        registered = await admin.post(
            "/api/v1/identity/register",
            json={"pubkey": pub, "display_name": "Owner"},
            headers={"X-Admin-Token": _ADMIN},
        )
    assert registered.status_code == 200, registered.text

    app = _proxy_app(monkeypatch, keypair, httpx.ASGITransport(app=backend))
    resp = await _call(app, "GET", "/api/board/federation/get", params={"path": "/api/v1/me"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["pubkey"] == pub


@pytest.mark.asyncio
async def test_caller_body_cannot_choose_the_signed_endpoint(
    monkeypatch: pytest.MonkeyPatch, keypair: tuple[str, str]
) -> None:
    """The UI supplies the body; a smuggled ``aud`` would let it obtain a
    signature for a different endpoint than the allow-listed path."""
    seen: list[dict[str, Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    app = _proxy_app(monkeypatch, keypair, httpx.MockTransport(_handler))
    resp = await _call(
        app,
        "POST",
        "/api/board/federation/post",
        json={"path": "/api/v1/activities", "body": {"aud": "GET /api/v1/friends", "x": 1}},
    )
    assert resp.status_code == 200, resp.text
    assert seen[0]["aud"] == "POST /api/v1/activities"
