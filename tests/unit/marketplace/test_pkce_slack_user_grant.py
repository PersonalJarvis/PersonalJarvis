"""Slack PKCE user grants: nested exchange answer, top-level refresh answer.

A Slack PKCE app is a public client: neither the code exchange nor the refresh
may carry a client_secret when none is configured. The exchange nests the user
token under ``authed_user``; a user-token refresh answers at the top level.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from jarvis.marketplace.auth.oauth_pkce_loopback import (
    PkceLoopbackConfig,
    PkceLoopbackHandler,
    _PendingPkceFlow,
)
from jarvis.marketplace.token_store import Tokens

_ACCESS = "xoxe.xoxp-1-user"
_ROTATED = "xoxe-1-rotated"
_FIRST = "xoxe-1-first"


def _config() -> PkceLoopbackConfig:
    return PkceLoopbackConfig(
        plugin_id="slack",
        authorization_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",  # noqa: S106
        client_id="111.222",
        callback_port=0,
        scopes=["search:read", "chat:write"],
        scope_param_name="user_scope",
    )


def _capture(monkeypatch: pytest.MonkeyPatch, payload: dict) -> dict:
    captured: dict = {}

    async def _fake_post(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured["url"] = url
        captured["data"] = kwargs["data"]
        captured["headers"] = kwargs.get("headers", {})
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    return captured


@pytest.mark.asyncio
async def test_exchange_reads_nested_user_token_without_secret(monkeypatch):
    captured = _capture(
        monkeypatch,
        {
            "ok": True,
            "app_id": "A1",
            "authed_user": {
                "id": "U1",
                "scope": "search:read,chat:write",
                "access_token": _ACCESS,
                "token_type": "user",
                "refresh_token": _FIRST,
                "expires_in": 43200,
            },
            "team": {"id": "T1", "name": "Team"},
            "enterprise": None,
            "is_enterprise_install": False,
        },
    )
    cfg = _config()
    pending = _PendingPkceFlow(
        config=cfg,
        callback_server=None,
        code_verifier="verifier",
        redirect_uri="http://127.0.0.1:3118/oauth/callback",
    )

    before = datetime.now(UTC)
    tokens = await PkceLoopbackHandler(cfg)._exchange(pending, code="code")  # noqa: SLF001

    assert "client_secret" not in captured["data"]
    assert "Authorization" not in captured["headers"]
    assert captured["data"]["code_verifier"] == "verifier"
    assert tokens.access == _ACCESS
    assert tokens.refresh == _FIRST
    assert tokens.expires_at is not None
    assert before + timedelta(hours=11) < tokens.expires_at < before + timedelta(hours=13)
    assert tokens.extra["scope"] == "search:read,chat:write"
    assert tokens.extra["team_id"] == "T1"
    assert tokens.extra["token_endpoint_auth_method"] == "none"  # noqa: S105 — a method name
    assert "client_secret" not in tokens.extra


@pytest.mark.asyncio
async def test_exchange_never_pairs_a_bot_refresh_with_the_user_token(monkeypatch):
    _capture(
        monkeypatch,
        {
            "ok": True,
            "access_token": "xoxb-bot",
            "refresh_token": "xoxe-1-bot-refresh",
            "expires_in": 999,
            "authed_user": {"id": "U1", "access_token": _ACCESS},
        },
    )
    cfg = _config()
    pending = _PendingPkceFlow(
        config=cfg, callback_server=None, code_verifier="v", redirect_uri="http://x"
    )
    tokens = await PkceLoopbackHandler(cfg)._exchange(pending, code="code")  # noqa: SLF001
    assert tokens.access == _ACCESS
    assert tokens.refresh is None
    assert tokens.expires_at is None


@pytest.mark.asyncio
async def test_exchange_tolerates_null_authed_user(monkeypatch):
    _capture(
        monkeypatch,
        {"ok": True, "authed_user": None, "access_token": _ACCESS, "expires_in": "60"},
    )
    cfg = _config()
    pending = _PendingPkceFlow(
        config=cfg, callback_server=None, code_verifier="v", redirect_uri="http://x"
    )
    tokens = await PkceLoopbackHandler(cfg)._exchange(pending, code="code")  # noqa: SLF001
    assert tokens.access == _ACCESS
    assert tokens.expires_at is not None


@pytest.mark.asyncio
async def test_refresh_reads_top_level_user_token_and_rotates(monkeypatch):
    captured = _capture(
        monkeypatch,
        {
            "ok": True,
            "access_token": "xoxe.xoxp-1-renewed",
            "expires_in": 43200,
            "refresh_token": _ROTATED,
            "token_type": "user",
            "scope": "search:read,chat:write",
        },
    )
    current = Tokens(
        access=_ACCESS,
        refresh=_FIRST,
        extra={"client_id": "111.222", "token_endpoint_auth_method": "none"},
    )
    tokens = await PkceLoopbackHandler(_config()).refresh(current)

    assert captured["data"] == {
        "grant_type": "refresh_token",
        "refresh_token": _FIRST,
        "client_id": "111.222",
    }
    assert tokens.access == "xoxe.xoxp-1-renewed"
    assert tokens.refresh == _ROTATED
    assert tokens.expires_at is not None
    assert "client_secret" not in tokens.extra


@pytest.mark.asyncio
async def test_refresh_also_accepts_nested_user_token(monkeypatch):
    _capture(
        monkeypatch,
        {
            "ok": True,
            "authed_user": {
                "access_token": "xoxe.xoxp-1-nested",
                "refresh_token": _ROTATED,
                "expires_in": 43200,
            },
        },
    )
    current = Tokens(access=_ACCESS, refresh=_FIRST, extra={"client_id": "111.222"})
    tokens = await PkceLoopbackHandler(_config()).refresh(current)
    assert tokens.access == "xoxe.xoxp-1-nested"
    assert tokens.refresh == _ROTATED


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["invalid_refresh_token", "token_expired", "token_revoked"])
async def test_dead_slack_refresh_token_reports_revoked(monkeypatch, code):
    _capture(monkeypatch, {"ok": False, "error": code})
    current = Tokens(access=_ACCESS, refresh=_FIRST, extra={"client_id": "111.222"})
    with pytest.raises(RuntimeError, match="^revoked$"):
        await PkceLoopbackHandler(_config()).refresh(current)
