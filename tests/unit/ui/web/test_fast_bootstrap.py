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
import json
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import pytest

from jarvis.ui.web.fast_bootstrap import FastBootstrap
from jarvis.ui.web.surface_security import COOKIE_NAME, SurfaceSecurity

_TOKEN = "fast-bootstrap-test-session"  # noqa: S105


async def _drive(app: Any, scope: dict, body: bytes = b"") -> list[dict]:
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    await app(scope, receive, send)
    return sent


def _http(path: str, method: str = "GET", *, cookie: str | None = None) -> dict:
    headers = [
        (b"host", b"127.0.0.1:47821"),
        (b"origin", b"http://127.0.0.1:47821"),
    ]
    if cookie is not None:
        headers.append((b"cookie", f"{COOKIE_NAME}={cookie}".encode("ascii")))
    return {
        "type": "http",
        "method": method,
        "path": path,
        "scheme": "http",
        "client": ("127.0.0.1", 50000),
        "headers": headers,
    }


async def _exchange(bs: FastBootstrap, token: str = _TOKEN) -> str:
    body = json.dumps({"session_token": token}).encode("utf-8")
    sent = await _drive(
        bs.app,
        _http("/api/ui/session", method="POST"),
        body,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 204
    raw_cookie = next(value for key, value in start["headers"] if key == b"set-cookie")
    parsed = SimpleCookie()
    parsed.load(raw_cookie.decode("latin-1"))
    return parsed[COOKIE_NAME].value


@pytest.mark.asyncio
async def test_health_returns_200_before_real_app_set() -> None:
    bs = FastBootstrap(session_token=_TOKEN)
    sent = await _drive(bs.app, _http("/api/health"))
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200


@pytest.mark.asyncio
async def test_non_health_request_holds_until_app_set_then_delegates() -> None:
    bs = FastBootstrap(session_token=_TOKEN)
    cookie = await _exchange(bs)
    seen: dict[str, str] = {}

    async def real_app(scope: dict, receive: Any, send: Any) -> None:
        seen["path"] = scope["path"]
        await send({"type": "http.response.start", "status": 201, "headers": []})
        await send({"type": "http.response.body", "body": b"real"})

    task = asyncio.create_task(
        _drive(bs.app, _http("/api/missions", cookie=cookie))
    )
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
    bs = FastBootstrap(session_token=_TOKEN)
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
    bs = FastBootstrap(hold_timeout=0.05, session_token=_TOKEN)
    cookie = await _exchange(bs)
    sent = await _drive(bs.app, _http("/api/missions", cookie=cookie))
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 503


@pytest.mark.asyncio
async def test_websocket_held_then_closed_on_timeout() -> None:
    bs = FastBootstrap(hold_timeout=0.05, session_token=_TOKEN)
    cookie = await _exchange(bs)
    scope = {
        "type": "websocket",
        "path": "/ws",
        "scheme": "ws",
        "client": ("127.0.0.1", 50000),
        "headers": _http("/", cookie=cookie)["headers"],
    }
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
    bs = FastBootstrap(dist_dir=_seed_dist(tmp_path), session_token=_TOKEN)
    sent = await _drive(bs.app, _http("/"))
    assert _status(sent) == 200
    assert b"<div id=root>" in _body(sent)
    headers = next(
        message["headers"]
        for message in sent
        if message["type"] == "http.response.start"
    )
    assert all(key != b"set-cookie" for key, _value in headers)


@pytest.mark.asyncio
async def test_bootstrap_token_only_exchanges_once_across_app_handoff() -> None:
    bs = FastBootstrap(session_token=_TOKEN)

    async def protected_app(scope: dict, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"protected"})

    # The raw WebView credential is not itself an authenticated cookie.
    rejected = await _drive(bs.app, _http("/api/config", cookie=_TOKEN))
    assert _status(rejected) == 401

    # Build the normal secured app before React receives the injected token.
    bs.set_app(SurfaceSecurity(protected_app))
    cookie = await _exchange(bs)
    replay = await _drive(
        bs.app,
        _http("/api/ui/session", method="POST"),
        json.dumps({"session_token": _TOKEN}).encode("utf-8"),
    )
    assert _status(replay) == 401

    protected = await _drive(bs.app, _http("/api/config", cookie=cookie))
    assert _status(protected) == 200


@pytest.mark.asyncio
async def test_serves_static_asset_immediately_before_app_ready(tmp_path) -> None:
    bs = FastBootstrap(dist_dir=_seed_dist(tmp_path), session_token=_TOKEN)
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
    bs = FastBootstrap(dist_dir=_seed_dist(tmp_path), session_token=_TOKEN)
    sent = await _drive(bs.app, _http("/wiki/some/page"))
    assert _status(sent) == 200
    assert b"<div id=root>" in _body(sent)


@pytest.mark.asyncio
async def test_api_request_is_still_held_not_served_as_static(tmp_path) -> None:
    # /api/* must NOT be served as static — it is the dynamic surface and must
    # be held until the real app is ready.
    bs = FastBootstrap(
        dist_dir=_seed_dist(tmp_path), hold_timeout=0.05, session_token=_TOKEN
    )
    cookie = await _exchange(bs)
    sent = await _drive(bs.app, _http("/api/missions", cookie=cookie))
    assert _status(sent) == 503  # held → warming timeout, never a static 200


@pytest.mark.asyncio
async def test_shell_served_event_fires_after_js_bundle(tmp_path) -> None:
    # The backend defers its GIL-heavy build until the entry JS bundle is out,
    # so the UI paints first. index.html alone (blank #root) must NOT satisfy it.
    bs = FastBootstrap(dist_dir=_seed_dist(tmp_path), session_token=_TOKEN)
    await _drive(bs.app, _http("/"))  # index.html → not enough on its own
    assert not await bs.wait_shell_served(timeout=0.05)
    await _drive(bs.app, _http("/assets/app.js"))  # entry bundle → now ready
    assert await bs.wait_shell_served(timeout=0.05)


@pytest.mark.asyncio
async def test_shell_paint_requires_browser_ack_not_just_js_bytes(tmp_path) -> None:
    # Sending the bundle does not prove the browser painted it. The desktop
    # backend must stay clear of heavy imports until the boot page confirms a
    # visual frame through the dedicated warm-up endpoint.
    bs = FastBootstrap(dist_dir=_seed_dist(tmp_path), session_token=_TOKEN)
    await _drive(bs.app, _http("/"))
    await _drive(bs.app, _http("/assets/app.js"))
    assert not await bs.wait_shell_painted(timeout=0.05)

    sent = await _drive(bs.app, _http("/api/ui/shell-painted", method="POST"))

    assert _status(sent) == 204
    assert await bs.wait_shell_painted(timeout=0.05)


def test_boot_page_acknowledges_after_two_frames() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    page = repo_root / "jarvis/ui/web/frontend/index.html"
    html = page.read_text(encoding="utf-8")
    assert "/api/ui/shell-painted" in html
    assert html.count("requestAnimationFrame") >= 2


# --- listener self-healing (the lock-zombie root cause) ---------------------
# Forensic 2026-06-26: a transient Windows socket error (OSError WinError 64,
# "the network name is no longer available") killed the asyncio proactor accept
# loop, which closes the LISTENING socket. uvicorn never re-binds it, so the
# port went dead permanently while the rest of the process (voice, telegram)
# lived on — a "lock-zombie" that held the single-instance lock with no port and
# no window, so every restart bounced. The bootstrap owns the socket, so it must
# notice the dead listener and re-bind itself.


def _free_port() -> int:
    import socket as _socket

    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:  # noqa: ASYNC109 — test helper, conventional timeout param
    from contextlib import suppress

    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()
        return True
    except (TimeoutError, OSError):
        return False


@pytest.mark.asyncio
async def test_serve_starts_a_listener_that_answers_health() -> None:
    port = _free_port()
    bs = FastBootstrap(session_token=_TOKEN)
    await bs.serve("127.0.0.1", port, supervise=False)
    try:
        assert await _port_open("127.0.0.1", port)
    finally:
        await bs.stop()


@pytest.mark.asyncio
async def test_supervisor_rebinds_listener_after_socket_death() -> None:
    # Reproduce the forensic: while serving, the OS kills the listening socket
    # (simulated by closing the uvicorn server socket exactly as the proactor's
    # ``sock.close()`` does on WinError 64). The supervisor must detect the dead
    # port and re-bind, so the port comes back WITHOUT a process restart.
    port = _free_port()
    bs = FastBootstrap(session_token=_TOKEN)
    await bs.serve("127.0.0.1", port, supervise=True, supervise_interval=0.2)
    try:
        assert await _port_open("127.0.0.1", port), "listener must be up after serve"

        # Kill the listening socket out from under uvicorn (what WinError 64 does).
        await bs._kill_listener_for_test()
        assert not await _port_open("127.0.0.1", port), "port must be dead after socket close"

        # The supervisor probes on its interval and re-binds. Give it a few cycles.
        recovered = False
        for _ in range(30):
            await asyncio.sleep(0.2)
            if await _port_open("127.0.0.1", port):
                recovered = True
                break
        assert recovered, "supervisor must re-bind the dead listener (self-heal)"
    finally:
        await bs.stop()


@pytest.mark.asyncio
async def test_rebind_preserves_registered_real_app() -> None:
    # A re-bind after a socket death must keep delegating to the already
    # registered real app — the warm-up state survives the listener swap.
    port = _free_port()
    bs = FastBootstrap(session_token=_TOKEN)

    async def real_app(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await FastBootstrap._handle_lifespan(receive, send)
            return
        await send({"type": "http.response.start", "status": 222, "headers": []})
        await send({"type": "http.response.body", "body": b"real"})

    await bs.serve("127.0.0.1", port, supervise=True, supervise_interval=0.2)
    try:
        bs.set_app(real_app)
        await bs._kill_listener_for_test()
        # wait for self-heal
        for _ in range(30):
            await asyncio.sleep(0.2)
            if await _port_open("127.0.0.1", port):
                break
        # the rebound listener still delegates to the real app
        sent = await _drive(bs.app, _http("/api/whoami"))
        assert _status(sent) == 222
    finally:
        await bs.stop()
