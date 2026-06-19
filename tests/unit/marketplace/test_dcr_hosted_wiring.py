"""HostedMcpDcrHandler must use the hosted callback when one is configured
(Wave 2, #2). DCR registers its redirect_uri dynamically, so a public HTTPS
callback works — this is the headless-VPS path. With no public base URL the
handler falls back to the loopback callback (desktop).
"""
from __future__ import annotations

import pytest

from jarvis.marketplace.auth.oauth_dcr import DcrConfig, HostedMcpDcrHandler
from jarvis.marketplace.hosted_callback import _PENDING, set_public_callback_base_url

HOSTED = "https://jarvis.example.com"
HOSTED_CB = "https://jarvis.example.com/api/marketplace/oauth/callback"


@pytest.fixture(autouse=True)
def _clean():
    _PENDING.clear()
    set_public_callback_base_url("")
    yield
    _PENDING.clear()
    set_public_callback_base_url("")


def _patch_network(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    captured: dict[str, str] = {}

    async def _fake_discover(self, client):  # noqa: ANN001
        return {
            "authorization_endpoint": "https://auth.example/authorize",
            "token_endpoint": "https://auth.example/token",
            "registration_endpoint": "https://auth.example/register",
        }

    async def _fake_register(self, client, registration_endpoint, redirect_uri):  # noqa: ANN001
        captured["redirect_uri"] = redirect_uri
        return "client-xyz"

    monkeypatch.setattr(HostedMcpDcrHandler, "_discover", _fake_discover)
    monkeypatch.setattr(HostedMcpDcrHandler, "_register", _fake_register)
    return captured


def _handler() -> HostedMcpDcrHandler:
    return HostedMcpDcrHandler(
        DcrConfig(
            plugin_id="notion",
            discovery_url="https://example/.well-known/oauth-protected-resource",
        )
    )


async def _cleanup(handler: HostedMcpDcrHandler) -> None:
    for pending in list(handler._pending.values()):  # noqa: SLF001
        await pending.callback_server.stop()


@pytest.mark.asyncio
async def test_dcr_start_uses_hosted_callback_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_public_callback_base_url(HOSTED)
    captured = _patch_network(monkeypatch)
    handler = _handler()

    session = await handler.start(object())
    try:
        assert session.kind == "browser_redirect"
        # open_url percent-encodes redirect_uri; the host token survives.
        assert "jarvis.example.com" in session.open_url
        # DCR must register the hosted redirect_uri, not a 127.0.0.1 loopback.
        assert captured["redirect_uri"] == HOSTED_CB
        # The hosted server parked its state for the public route to resolve.
        assert len(_PENDING) == 1
    finally:
        await _cleanup(handler)


@pytest.mark.asyncio
async def test_dcr_start_uses_loopback_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_network(monkeypatch)
    handler = _handler()

    session = await handler.start(object())
    try:
        assert "127.0.0.1" in session.open_url
        assert captured["redirect_uri"].startswith("http://127.0.0.1:")
        # Loopback mode must not touch the hosted pending registry.
        assert len(_PENDING) == 0
    finally:
        await _cleanup(handler)
