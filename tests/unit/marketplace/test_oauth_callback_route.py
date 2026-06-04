"""The public hosted OAuth callback route (Wave 2, #2).

GET /api/marketplace/oauth/callback?code=&state=&error= delivers the captured
redirect to the waiting HostedCallbackServer via deliver_callback.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from jarvis.marketplace.hosted_callback import (
    _PENDING,
    HostedCallbackServer,
    set_public_callback_base_url,
)
from jarvis.ui.web.marketplace_routes import router


@pytest.fixture(autouse=True)
def _clean():
    _PENDING.clear()
    set_public_callback_base_url("")
    yield
    _PENDING.clear()
    set_public_callback_base_url("")


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_callback_route_delivers_code() -> None:
    srv = HostedCallbackServer(expected_state="ST", base_url="https://x.test")
    await srv.start()
    async with _client() as client:
        resp = await client.get(
            "/api/marketplace/oauth/callback",
            params={"code": "C123", "state": "ST"},
        )
    assert resp.status_code == 200
    assert "Connected" in resp.text
    result = await asyncio.wait_for(srv.await_callback(), 1.0)
    assert result.code == "C123"
    assert result.state == "ST"


@pytest.mark.asyncio
async def test_callback_route_unknown_state_is_400() -> None:
    async with _client() as client:
        resp = await client.get(
            "/api/marketplace/oauth/callback",
            params={"code": "C", "state": "does-not-exist"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_route_provider_error_is_400() -> None:
    srv = HostedCallbackServer(expected_state="ERR", base_url="https://x.test")
    await srv.start()
    async with _client() as client:
        resp = await client.get(
            "/api/marketplace/oauth/callback",
            params={"state": "ERR", "error": "access_denied"},
        )
    assert resp.status_code == 400
    with pytest.raises(RuntimeError, match="access_denied"):
        await srv.await_callback()
