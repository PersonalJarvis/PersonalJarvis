"""Unit tests for jarvis.marketplace.oauth_discord.

No network: a fake httpx.AsyncClient answers the request and records it.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from jarvis.marketplace import oauth_discord as do


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeClient:
    instances: list[_FakeClient] = []

    def __init__(self, response: _FakeResponse | Exception, **kwargs: object) -> None:
        self._response = response
        self.requests: list[dict[str, object]] = []
        _FakeClient.instances.append(self)

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        self.requests.append({"url": url, "headers": headers or {}})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


@pytest.fixture(autouse=True)
def _clean_instances():
    _FakeClient.instances.clear()
    yield
    _FakeClient.instances.clear()


def _patch_client(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse | Exception):
    monkeypatch.setattr(do.httpx, "AsyncClient", lambda **kwargs: _FakeClient(response, **kwargs))


# --- URL builders -----------------------------------------------------------


def test_bot_invite_url_uses_minimal_permissions() -> None:
    url = do.build_bot_invite_url("1234")
    parts = urlparse(url)
    assert parts.scheme == "https"
    assert "discord.com" in parts.netloc
    query = parse_qs(parts.query)
    assert query["client_id"] == ["1234"]
    assert query["scope"] == ["bot"]
    assert query["permissions"] == [str(do.MINIMAL_BOT_PERMISSIONS)]
    assert "response_type" not in query
    assert "redirect_uri" not in query


def test_minimal_permissions_are_view_send_history_only() -> None:
    assert do.MINIMAL_BOT_PERMISSIONS == (1 << 10) | (1 << 11) | (1 << 16) == 68608
    assert not do.MINIMAL_BOT_PERMISSIONS & (1 << 3)  # Administrator bit never set


def test_bot_invite_url_preselects_guild() -> None:
    url = do.build_bot_invite_url("1234", guild_id="999", disable_guild_select=True)
    query = parse_qs(urlparse(url).query)
    assert query["guild_id"] == ["999"]
    assert query["disable_guild_select"] == ["true"]


def test_authorize_url_matches_official_shape() -> None:
    url = do.build_authorize_url(
        client_id="cid", redirect_uri="http://127.0.0.1:3129/oauth/callback", state="s3"
    )
    query = parse_qs(urlparse(url).query)
    assert url.startswith(do.AUTHORIZATION_URL)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["cid"]
    assert query["state"] == ["s3"]
    assert query["prompt"] == ["consent"]
    assert "identify" in query["scope"][0].split(" ")


def test_authorize_url_adds_pkce_challenge() -> None:
    url = do.build_authorize_url(
        client_id="cid", redirect_uri="http://x/", state="s", code_challenge="ch"
    )
    query = parse_qs(urlparse(url).query)
    assert query["code_challenge"] == ["ch"]
    assert query["code_challenge_method"] == ["S256"]


def test_browser_scopes_are_free_and_approval_free() -> None:
    assert "identify" in do.BROWSER_SCOPES
    for gated in ("bot", "email", "voice", "activities.read", "dm_channels.read", "rpc"):
        assert gated not in do.BROWSER_SCOPES


def test_official_endpoint_constants() -> None:
    assert do.TOKEN_URL == "https://discord.com/api/oauth2/token"  # noqa: S105 — public endpoint URL
    assert do.REVOCATION_URL == "https://discord.com/api/oauth2/token/revoke"
    assert do.CURRENT_USER_URL == "https://discord.com/api/v10/users/@me"
    assert do.CALLBACK_PORT == 3129


# --- fetch_current_user -----------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_current_user_returns_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(
        monkeypatch,
        _FakeResponse(200, {"id": "4242", "username": "alice", "global_name": "Alice"}),
    )
    identity = await do.fetch_current_user("user-token")
    assert identity.user_id == "4242"
    assert identity.username == "alice"
    assert identity.global_name == "Alice"
    recorded = _FakeClient.instances[0].requests[0]
    assert recorded["url"] == do.CURRENT_USER_URL
    assert recorded["headers"]["Authorization"] == "Bearer user-token"


@pytest.mark.asyncio
async def test_fetch_current_user_rejects_bot_scheme_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Discord answers 401 when a Bot token is sent as Bearer: the schemes
    # are not interchangeable, and the error must say so safely.
    _patch_client(monkeypatch, _FakeResponse(401, {"message": "401: Unauthorized"}))
    with pytest.raises(do.DiscordApiError, match="rejected"):
        await do.fetch_current_user("bot-token-sent-as-bearer")


@pytest.mark.asyncio
async def test_fetch_current_user_rejects_missing_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, _FakeResponse(200, {"username": "noid"}))
    with pytest.raises(do.DiscordApiError, match="unusable"):
        await do.fetch_current_user("user-token")


@pytest.mark.asyncio
async def test_fetch_current_user_rejects_empty_token() -> None:
    with pytest.raises(do.DiscordApiError, match="missing"):
        await do.fetch_current_user("")


@pytest.mark.asyncio
async def test_fetch_current_user_maps_network_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, httpx.ConnectError("dns down"))
    with pytest.raises(do.DiscordApiError, match="ConnectError"):
        await do.fetch_current_user("user-token")
