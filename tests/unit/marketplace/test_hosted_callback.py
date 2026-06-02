"""Unit tests for the hosted OAuth callback rendezvous (Wave 2, #2).

The loopback ``OAuthCallbackServer`` needs the browser to reach 127.0.0.1.
On a headless VPS the provider redirects to a public HTTPS route on the main
app instead; ``HostedCallbackServer`` parks a per-flow Future keyed by the
OAuth ``state`` and ``deliver_callback`` (called by that route) hands the
captured code to the waiting flow. ``make_callback_server`` selects hosted vs
loopback by config so redirect handlers swap one construction line only.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.marketplace.hosted_callback import (
    _PENDING,
    HostedCallbackServer,
    deliver_callback,
    get_public_callback_base_url,
    make_callback_server,
    set_public_callback_base_url,
)
from jarvis.marketplace.oauth_callback_server import (
    CallbackResult,
    CallbackTimeoutError,
    OAuthCallbackServer,
)


@pytest.fixture(autouse=True)
def _clean_state():
    _PENDING.clear()
    set_public_callback_base_url("")
    yield
    _PENDING.clear()
    set_public_callback_base_url("")


# --------------------------------------------------------------- redirect_uri

def test_redirect_uri_uses_hosted_base() -> None:
    srv = HostedCallbackServer(expected_state="abc", base_url="https://jarvis.example.com/")
    assert srv.redirect_uri == "https://jarvis.example.com/api/marketplace/oauth/callback"


def test_empty_state_or_base_rejected() -> None:
    with pytest.raises(ValueError):
        HostedCallbackServer(expected_state="", base_url="https://x.test")
    with pytest.raises(ValueError):
        HostedCallbackServer(expected_state="s", base_url="")


# ------------------------------------------------------------ deliver_callback

@pytest.mark.asyncio
async def test_deliver_callback_resolves_await() -> None:
    srv = HostedCallbackServer(expected_state="st8", base_url="https://x.test")
    await srv.start()

    async def _deliver() -> None:
        await asyncio.sleep(0.01)
        assert deliver_callback(code="CODE123", state="st8") is True

    asyncio.create_task(_deliver())
    result = await srv.await_callback()
    assert isinstance(result, CallbackResult)
    assert result.code == "CODE123"
    assert result.state == "st8"
    await srv.stop()


def test_deliver_unknown_state_returns_false() -> None:
    assert deliver_callback(code="x", state="does-not-exist") is False


@pytest.mark.asyncio
async def test_deliver_with_error_sets_exception() -> None:
    srv = HostedCallbackServer(expected_state="errst", base_url="https://x.test")
    await srv.start()
    assert deliver_callback(code="", state="errst", error="access_denied") is True
    with pytest.raises(RuntimeError, match="access_denied"):
        await srv.await_callback()
    await srv.stop()


@pytest.mark.asyncio
async def test_deliver_missing_code_sets_exception() -> None:
    srv = HostedCallbackServer(expected_state="nocode", base_url="https://x.test")
    await srv.start()
    assert deliver_callback(code="", state="nocode") is True
    with pytest.raises(RuntimeError, match="code"):
        await srv.await_callback()
    await srv.stop()


@pytest.mark.asyncio
async def test_await_callback_times_out() -> None:
    srv = HostedCallbackServer(
        expected_state="tos", base_url="https://x.test", timeout_seconds=0.1
    )
    await srv.start()
    with pytest.raises(CallbackTimeoutError):
        await srv.await_callback()
    await srv.stop()


@pytest.mark.asyncio
async def test_start_registers_and_stop_deregisters() -> None:
    srv = HostedCallbackServer(expected_state="dreg", base_url="https://x.test")
    await srv.start()
    assert "dreg" in _PENDING
    await srv.stop()
    assert "dreg" not in _PENDING


@pytest.mark.asyncio
async def test_await_deregisters_after_success() -> None:
    srv = HostedCallbackServer(expected_state="dreg2", base_url="https://x.test")
    await srv.start()
    deliver_callback(code="C", state="dreg2")
    await srv.await_callback()
    assert "dreg2" not in _PENDING
    await srv.stop()


# --------------------------------------------------------------------- factory

def test_factory_returns_loopback_when_no_base_url() -> None:
    srv = make_callback_server("st", timeout_seconds=10)
    assert isinstance(srv, OAuthCallbackServer)


def test_factory_returns_hosted_when_base_url_set() -> None:
    set_public_callback_base_url("https://jarvis.example.com")
    assert get_public_callback_base_url() == "https://jarvis.example.com"
    srv = make_callback_server("st", timeout_seconds=10)
    assert isinstance(srv, HostedCallbackServer)
    assert srv.redirect_uri == "https://jarvis.example.com/api/marketplace/oauth/callback"


def test_factory_loopback_respects_fixed_port() -> None:
    srv = make_callback_server("st", timeout_seconds=10, fixed_port=3118)
    assert isinstance(srv, OAuthCallbackServer)
    assert srv._port == 3118  # noqa: SLF001


def test_loopback_empty_callback_path_omits_path_from_redirect_uri() -> None:
    srv = OAuthCallbackServer(expected_state="st", callback_path="", port=3120)
    assert srv.redirect_uri == "http://127.0.0.1:3120"


def test_loopback_empty_callback_path_registers_root_route() -> None:
    srv = OAuthCallbackServer(expected_state="st", callback_path="", port=3120)
    app = srv._build_app()  # noqa: SLF001
    assert any(getattr(route, "path", None) == "/" for route in app.routes)
