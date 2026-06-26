"""Unit tests for the reusable serve-first fast-boot bootstrap.

The bootstrap binds the admin port immediately and answers ``/api/health`` with
200 the moment it is up — so the desktop shell's ``_wait_for_backend`` poll
succeeds and the window appears — while the heavy real app builds behind it.
Every non-health request is held until the real app is registered, then
delegated to it (the "serve first, init behind" contract). These tests drive
the ASGI callable directly (no socket) to prove that behavior.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.ui.web.fast_bootstrap import FastBootstrap


async def _drive(app: Any, scope: dict) -> list[dict]:
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    await app(scope, receive, send)
    return sent


def _http(path: str, method: str = "GET") -> dict:
    return {"type": "http", "method": method, "path": path, "headers": []}


@pytest.mark.asyncio
async def test_health_returns_200_before_real_app_set() -> None:
    bs = FastBootstrap()
    sent = await _drive(bs.app, _http("/api/health"))
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200


@pytest.mark.asyncio
async def test_non_health_request_holds_until_app_set_then_delegates() -> None:
    bs = FastBootstrap()
    seen: dict[str, str] = {}

    async def real_app(scope: dict, receive: Any, send: Any) -> None:
        seen["path"] = scope["path"]
        await send({"type": "http.response.start", "status": 201, "headers": []})
        await send({"type": "http.response.body", "body": b"real"})

    task = asyncio.create_task(_drive(bs.app, _http("/api/missions")))
    await asyncio.sleep(0.02)
    assert not task.done(), "request must be held while the real app is warming"

    bs.set_app(real_app)
    sent = await asyncio.wait_for(task, timeout=2.0)
    assert seen["path"] == "/api/missions"
    assert any(
        m["type"] == "http.response.start" and m["status"] == 201 for m in sent
    )


@pytest.mark.asyncio
async def test_health_delegates_to_real_app_once_ready() -> None:
    bs = FastBootstrap()
    calls: list[str] = []

    async def real_app(scope: dict, receive: Any, send: Any) -> None:
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    bs.set_app(real_app)
    await _drive(bs.app, _http("/api/health"))
    assert calls == ["/api/health"], "once ready, even health must delegate to the real app"


@pytest.mark.asyncio
async def test_held_request_times_out_to_503_when_app_never_arrives() -> None:
    bs = FastBootstrap(hold_timeout=0.05)
    sent = await _drive(bs.app, _http("/api/missions"))
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 503


@pytest.mark.asyncio
async def test_websocket_held_then_closed_on_timeout() -> None:
    bs = FastBootstrap(hold_timeout=0.05)
    scope = {"type": "websocket", "path": "/ws", "headers": []}
    sent = await _drive(bs.app, scope)
    assert any(m["type"] == "websocket.close" for m in sent)


# --- static frontend served immediately (no black screen) -------------------


def _seed_dist(tmp_path) -> object:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><body><div id=root></div>"
        "<script src=/assets/app.js></script></body></html>",
        encoding="utf-8",
    )
    (dist / "assets" / "app.js").write_text("console.log('jarvis ui')", encoding="utf-8")
    return dist


def _body(sent: list[dict]) -> bytes:
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def _status(sent: list[dict]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.mark.asyncio
async def test_serves_index_html_immediately_before_app_ready(tmp_path) -> None:
    # The whole point: GET / returns the REAL index.html while warming, so the
    # desktop window shows the UI shell instead of a black screen.
    bs = FastBootstrap(dist_dir=_seed_dist(tmp_path))
    sent = await _drive(bs.app, _http("/"))
    assert _status(sent) == 200
    assert b"<div id=root>" in _body(sent)


@pytest.mark.asyncio
async def test_serves_static_asset_immediately_before_app_ready(tmp_path) -> None:
    bs = FastBootstrap(dist_dir=_seed_dist(tmp_path))
    sent = await _drive(bs.app, _http("/assets/app.js"))
    assert _status(sent) == 200
    assert b"jarvis ui" in _body(sent)
    ctype = next(
        v for (k, v) in next(m for m in sent if m["type"] == "http.response.start")["headers"]
        if k == b"content-type"
    )
    assert b"javascript" in ctype


@pytest.mark.asyncio
async def test_spa_fallback_serves_index_for_unknown_route(tmp_path) -> None:
    # A deep-link to a client-side route (no real file) must fall back to
    # index.html so the SPA router can take over — NOT be held or 404'd.
    bs = FastBootstrap(dist_dir=_seed_dist(tmp_path))
    sent = await _drive(bs.app, _http("/wiki/some/page"))
    assert _status(sent) == 200
    assert b"<div id=root>" in _body(sent)


@pytest.mark.asyncio
async def test_api_request_is_still_held_not_served_as_static(tmp_path) -> None:
    # /api/* must NOT be served as static — it is the dynamic surface and must
    # be held until the real app is ready.
    bs = FastBootstrap(dist_dir=_seed_dist(tmp_path), hold_timeout=0.05)
    sent = await _drive(bs.app, _http("/api/missions"))
    assert _status(sent) == 503  # held → warming timeout, never a static 200


@pytest.mark.asyncio
async def test_shell_served_event_fires_after_js_bundle(tmp_path) -> None:
    # The backend defers its GIL-heavy build until the entry JS bundle is out,
    # so the UI paints first. index.html alone (blank #root) must NOT satisfy it.
    bs = FastBootstrap(dist_dir=_seed_dist(tmp_path))
    await _drive(bs.app, _http("/"))  # index.html → not enough on its own
    assert not await bs.wait_shell_served(timeout=0.05)
    await _drive(bs.app, _http("/assets/app.js"))  # entry bundle → now ready
    assert await bs.wait_shell_served(timeout=0.05)
