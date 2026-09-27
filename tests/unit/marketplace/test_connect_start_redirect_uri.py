"""connect_start surfaces the exact callback address the provider must call.

Regression context: a Slack browser login opened the provider page, the user
signed in, and then nothing ever happened — the pending dialog spun silently
because nobody could see WHERE the provider had to call back
(http://127.0.0.1:3118/oauth/callback, which a self-registered provider app
must explicitly allow). The start response now carries `redirect_uri` so the
UI can show it while waiting.
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import BackgroundTasks

from jarvis.marketplace.auth.base import AuthSession, FlowResult
from jarvis.marketplace.auth.oauth_pkce_loopback import (
    PkceLoopbackConfig,
    PkceLoopbackHandler,
)
from jarvis.marketplace.token_store import Tokens
from jarvis.ui.web import marketplace_routes as mr


async def _start_slack_like_session() -> tuple[PkceLoopbackHandler, AuthSession]:
    handler = PkceLoopbackHandler(
        PkceLoopbackConfig(
            plugin_id="slack",
            authorization_url="https://slack.com/oauth/v2/authorize",
            token_url="https://slack.com/api/oauth.v2.access",  # noqa: S106
            client_id="test-client-id",
            callback_port=0,  # ephemeral: never clash with a live 3118 listener
            scopes=["chat:write", "users:read"],
            scope_param_name="user_scope",
            callback_path="/oauth/callback",
        )
    )
    return handler, await handler.start(object())


@pytest.mark.asyncio
async def test_pkce_start_reports_the_callback_it_listens_on() -> None:
    """The session names the loopback address the provider must redirect to,
    and the authorize URL points the provider exactly there."""
    handler, session = await _start_slack_like_session()
    try:
        assert session.kind == "browser_redirect"
        assert session.redirect_uri is not None
        assert session.redirect_uri.startswith("http://127.0.0.1:")
        assert session.redirect_uri.endswith("/oauth/callback")

        assert session.open_url is not None
        params = parse_qs(urlparse(session.open_url).query)
        assert params["redirect_uri"] == [session.redirect_uri]
        assert params["user_scope"] == ["chat:write,users:read"]
    finally:
        await handler.cancel(session)


class _Catalog:
    def __init__(self, specs):
        self.plugins = specs

    def by_id(self, plugin_id: str):
        return next((s for s in self.plugins if s.id == plugin_id), None)


def _pkce_spec(client_id: str):
    for spec in mr.load_catalog().plugins:
        if spec.auth is not None and getattr(spec.auth, "mode", "") == "oauth_pkce_loopback":
            return spec.model_copy(
                update={"auth": spec.auth.model_copy(update={"client_id": client_id})}
            )
    pytest.skip("no oauth_pkce_loopback plugin in catalog")


class _StubPkceHandler:
    """Stub that, unlike the older token-save stub, also reports redirect_uri."""

    def __init__(self, config) -> None:
        self.config = config

    async def start(self, plugin_spec) -> AuthSession:
        return AuthSession(
            flow_id=f"test-flow-{plugin_spec.id}",
            plugin_id=plugin_spec.id,
            kind="browser_redirect",
            open_url="https://provider.example/authorize",
            redirect_uri="http://127.0.0.1:3118/oauth/callback",
        )

    async def await_completion(self, session: AuthSession) -> FlowResult:
        return FlowResult(tokens=Tokens(access="tok-123"), error=None)


@pytest.mark.asyncio
async def test_connect_start_response_carries_redirect_uri(monkeypatch) -> None:
    """The wire response forwards the handler's redirect_uri (additive field)."""
    real_create_task = asyncio.create_task
    captured: list[asyncio.Task] = []

    def _capturing(coro, **kwargs):
        task = real_create_task(coro, **kwargs)
        captured.append(task)
        return task

    async def _verified(spec, tokens):
        return None

    monkeypatch.setattr(mr.asyncio, "create_task", _capturing)
    monkeypatch.setattr(mr, "verify_connection", _verified)
    monkeypatch.setattr(mr.TokenStore, "save", lambda self, plugin_id, tokens: None)
    monkeypatch.setattr(mr, "_refresh_plugin_in_live_registry", lambda plugin_id: None)
    spec = _pkce_spec("real-client-id.apps.example")
    monkeypatch.setattr(mr, "load_catalog", lambda: _Catalog([spec]))
    monkeypatch.setattr(
        "jarvis.marketplace.connect_helpers.resolve_pkce_client",
        lambda pid, cid, csec: ("real-client-id.apps.example", None),
    )
    monkeypatch.setattr(mr, "PkceLoopbackHandler", _StubPkceHandler)

    session = await mr.connect_start(spec.id, BackgroundTasks())
    for task in captured:
        await task

    assert session["redirect_uri"] == "http://127.0.0.1:3118/oauth/callback"
    assert session["open_url"] == "https://provider.example/authorize"
