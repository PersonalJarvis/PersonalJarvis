"""Shipped OAuth clients whose provider demands a secret go through a broker.

Google (Desktop client) and Slack (distributed app) refuse code exchange and
refresh without the client secret. The app never holds it: grants issued to
the SHIPPED client are exchanged and refreshed through the project's token
broker, which adds the secret server-side. Expert overrides and publisher
secrets keep talking to the provider directly. Fakes only: the HTTP layer is
replaced by a recording ``post`` and secrets by a dict lookup.
"""

# The "token_*" fields compared below hold family names and endpoint URLs,
# never credentials; bandit's password heuristic misreads them.
# ruff: noqa: S105

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from jarvis.marketplace.auth.oauth_pkce_loopback import (
    PkceLoopbackConfig,
    PkceLoopbackHandler,
    _PendingPkceFlow,
)
from jarvis.marketplace.catalog import OAuthPkceLoopbackAuth
from jarvis.marketplace.connect_helpers import build_pkce_config
from jarvis.marketplace.publisher_clients import (
    SHIPPED_PUBLIC_CLIENT_IDS,
    SHIPPED_TOKEN_BROKERS,
    broker_family_for_client,
    is_standard_ready,
    resolve_client,
)
from jarvis.marketplace.token_store import Tokens

GOOGLE_ID = SHIPPED_PUBLIC_CLIENT_IDS["google"]
SLACK_ID = SHIPPED_PUBLIC_CLIENT_IDS["slack"]
GOOGLE_BROKER = SHIPPED_TOKEN_BROKERS["google"]
SLACK_BROKER = SHIPPED_TOKEN_BROKERS["slack"]
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint, not a secret
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"  # noqa: S105 - endpoint, not a secret
PLACEHOLDER = "REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID"
EXPERT_SECRET = "expert-secret-value"  # noqa: S105 - test value
PUBLISHER_SECRET = "publisher-secret-value"  # noqa: S105 - test value
GOOGLE_PLUGINS = (
    "gmail",
    "google_drive",
    "google_calendar",
    "youtube_music",
    "youtube_studio",
    "google_cloud",
)


def _secrets(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    monkeypatch.setattr(
        "jarvis.core.config.get_secret",
        lambda key, env_fallback=None: values.get(key),
    )


class _Recorder:
    """Stands in for ``httpx.AsyncClient.post`` and remembers each call."""

    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[dict] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = self

        async def _post(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            recorder.calls.append({"url": url, **kwargs})
            return httpx.Response(recorder.status, json=recorder.payload)

        monkeypatch.setattr(httpx.AsyncClient, "post", _post)


def _google_auth() -> OAuthPkceLoopbackAuth:
    return OAuthPkceLoopbackAuth(
        mode="oauth_pkce_loopback",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url=GOOGLE_TOKEN_URL,
        client_id=PLACEHOLDER,
        callback_port=3121,
        callback_path="",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        offline_access=True,
    )


def _slack_auth() -> OAuthPkceLoopbackAuth:
    return OAuthPkceLoopbackAuth(
        mode="oauth_pkce_loopback",
        authorization_url="https://slack.com/oauth/v2/authorize",
        token_url=SLACK_TOKEN_URL,
        client_id="REPLACE_WITH_JARVIS_SLACK_APP_CLIENT_ID",
        callback_port=3118,
        scopes=["chat:write"],
        user_scopes_only=True,
    )


# ---------------------------------------------------------------------------
# Resolution precedence: own > publisher secret > shipped + broker > missing
# ---------------------------------------------------------------------------


def test_expert_override_wins_and_talks_to_google_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secrets(
        monkeypatch,
        {
            "google_oauth_client_id": "own.apps.googleusercontent.com",
            "google_oauth_client_secret": EXPERT_SECRET,
            "publisher_google_oauth_client_id": "pub.apps.googleusercontent.com",
            "publisher_google_oauth_client_secret": PUBLISHER_SECRET,
        },
    )
    resolved = resolve_client("gmail", PLACEHOLDER, None)
    assert (resolved.client_id, resolved.client_secret, resolved.source) == (
        "own.apps.googleusercontent.com",
        EXPERT_SECRET,
        "own",
    )
    assert resolved.token_broker is None
    assert build_pkce_config("gmail", _google_auth()).token_broker is None


def test_publisher_secret_beats_shipped_client_without_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secrets(
        monkeypatch,
        {
            "publisher_google_oauth_client_id": "pub.apps.googleusercontent.com",
            "publisher_google_oauth_client_secret": PUBLISHER_SECRET,
        },
    )
    resolved = resolve_client("gmail", PLACEHOLDER, None)
    assert (resolved.client_id, resolved.client_secret, resolved.source) == (
        "pub.apps.googleusercontent.com",
        PUBLISHER_SECRET,
        "publisher",
    )
    assert resolved.token_broker is None
    assert build_pkce_config("gmail", _google_auth()).token_broker is None


@pytest.mark.parametrize("plugin_id", GOOGLE_PLUGINS)
def test_shipped_client_uses_broker_with_zero_setup(
    monkeypatch: pytest.MonkeyPatch, plugin_id: str
) -> None:
    _secrets(monkeypatch, {})
    resolved = resolve_client(plugin_id, PLACEHOLDER, None)
    assert (resolved.client_id, resolved.client_secret, resolved.source) == (
        GOOGLE_ID,
        None,
        "publisher",
    )
    assert resolved.token_broker == "google"
    assert is_standard_ready(plugin_id, PLACEHOLDER) is True


def test_family_without_shipped_client_stays_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets(monkeypatch, {})
    monkeypatch.delitem(SHIPPED_PUBLIC_CLIENT_IDS, "google")
    resolved = resolve_client("gmail", PLACEHOLDER, None)
    assert resolved.source == "missing"
    assert resolved.token_broker is None
    assert is_standard_ready("gmail", PLACEHOLDER) is False


def test_shipped_slack_client_uses_its_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets(monkeypatch, {})
    config = build_pkce_config("slack", _slack_auth())
    assert config.client_id == SLACK_ID
    assert config.client_secret is None
    assert config.token_broker == "slack"
    assert config.scope_param_name == "user_scope"


def test_broker_family_lookup_is_exact() -> None:
    assert broker_family_for_client(GOOGLE_ID) == "google"
    assert broker_family_for_client(SLACK_ID) == "slack"
    assert broker_family_for_client(SHIPPED_PUBLIC_CLIENT_IDS["microsoft"]) is None
    assert broker_family_for_client("own.apps.googleusercontent.com") is None
    assert broker_family_for_client(None) is None


def test_brokers_are_https_on_the_project_host() -> None:
    for broker in SHIPPED_TOKEN_BROKERS.values():
        for url in filter(None, (broker.token_url, broker.redirect_uri)):
            parts = urlsplit(url)
            assert parts.scheme == "https"
            assert parts.hostname == "token.personaljarvis.ai"


# ---------------------------------------------------------------------------
# Code exchange
# ---------------------------------------------------------------------------


def _config(family: str | None, *, secret: str | None = None) -> PkceLoopbackConfig:
    return PkceLoopbackConfig(
        plugin_id="gmail",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url=GOOGLE_TOKEN_URL,
        client_id=GOOGLE_ID if family else "own.apps.googleusercontent.com",
        client_secret=secret,
        callback_port=3121,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        offline_access=True,
        token_broker=family,
    )


def _pending(config: PkceLoopbackConfig, redirect: str) -> _PendingPkceFlow:
    return _PendingPkceFlow(
        config=config,
        callback_server=None,
        code_verifier="v" * 43,
        redirect_uri=redirect,
    )


@pytest.mark.asyncio
async def test_exchange_goes_to_broker_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder({"access_token": "ya29.a", "refresh_token": "1//r", "expires_in": 3599})
    recorder.install(monkeypatch)
    config = _config("google")

    tokens = await PkceLoopbackHandler(config)._exchange(  # noqa: SLF001
        _pending(config, "http://127.0.0.1:3121"), code="4/code"
    )

    (call,) = recorder.calls
    assert call["url"] == GOOGLE_BROKER.token_url
    assert call["data"] == {
        "client_id": GOOGLE_ID,
        "code": "4/code",
        "code_verifier": "v" * 43,
        "grant_type": "authorization_code",
        "redirect_uri": "http://127.0.0.1:3121",
    }
    assert "Authorization" not in call["headers"]
    assert tokens.access == "ya29.a"
    assert tokens.extra["client_id"] == GOOGLE_ID
    assert tokens.extra["token_broker"] == "google"
    assert tokens.extra["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in tokens.extra


@pytest.mark.asyncio
async def test_expert_exchange_goes_to_google_with_own_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder({"access_token": "ya29.a", "refresh_token": "1//r", "expires_in": 3599})
    recorder.install(monkeypatch)
    config = _config(None, secret=EXPERT_SECRET)

    tokens = await PkceLoopbackHandler(config)._exchange(  # noqa: SLF001
        _pending(config, "http://127.0.0.1:3121"), code="4/code"
    )

    (call,) = recorder.calls
    assert call["url"] == GOOGLE_TOKEN_URL
    assert call["data"]["client_secret"] == EXPERT_SECRET
    assert tokens.extra["client_secret"] == EXPERT_SECRET
    assert "token_broker" not in tokens.extra


@pytest.mark.asyncio
async def test_broker_errors_surface_like_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Recorder({"error": "invalid_grant", "error_description": "Bad Request"}, 400).install(
        monkeypatch
    )
    config = _config("google")
    with pytest.raises(RuntimeError, match="HTTP 400"):
        await PkceLoopbackHandler(config)._exchange(  # noqa: SLF001
            _pending(config, "http://127.0.0.1:3121"), code="4/code"
        )


# ---------------------------------------------------------------------------
# Refresh — routed by the GRANT, not by today's config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_brokered_grant_refreshes_through_broker_even_after_expert_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder({"access_token": "ya29.new", "expires_in": 3599})
    recorder.install(monkeypatch)
    expert_config = _config(None, secret=EXPERT_SECRET)
    grant = Tokens(
        access="ya29.old",
        refresh="1//r",
        extra={"client_id": GOOGLE_ID, "token_broker": "google"},
    )

    tokens = await PkceLoopbackHandler(expert_config).refresh(grant)

    (call,) = recorder.calls
    assert call["url"] == GOOGLE_BROKER.token_url
    assert call["data"] == {
        "grant_type": "refresh_token",
        "refresh_token": "1//r",
        "client_id": GOOGLE_ID,
    }
    assert tokens.access == "ya29.new"
    assert tokens.refresh == "1//r"
    assert tokens.extra["token_broker"] == "google"
    assert "client_secret" not in tokens.extra


@pytest.mark.asyncio
async def test_expert_grant_refreshes_directly_even_when_shipped_client_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder({"access_token": "ya29.new", "expires_in": 3599})
    recorder.install(monkeypatch)
    grant = Tokens(
        access="ya29.old",
        refresh="1//r",
        extra={
            "client_id": "own.apps.googleusercontent.com",
            "client_secret": EXPERT_SECRET,
            "token_endpoint_auth_method": "client_secret_post",
        },
    )

    await PkceLoopbackHandler(_config("google")).refresh(grant)

    (call,) = recorder.calls
    assert call["url"] == GOOGLE_TOKEN_URL
    assert call["data"]["client_secret"] == EXPERT_SECRET


@pytest.mark.asyncio
async def test_shipped_grant_without_marker_still_refreshes_through_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder({"access_token": "ya29.new", "expires_in": 3599})
    recorder.install(monkeypatch)
    grant = Tokens(access="a", refresh="1//r", extra={"client_id": GOOGLE_ID})

    tokens = await PkceLoopbackHandler(_config(None, secret=EXPERT_SECRET)).refresh(grant)

    assert recorder.calls[0]["url"] == GOOGLE_BROKER.token_url
    assert "client_secret" not in recorder.calls[0]["data"]
    assert tokens.extra["token_broker"] == "google"


@pytest.mark.asyncio
async def test_legacy_grant_follows_current_brokered_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder({"access_token": "ya29.new", "expires_in": 3599})
    recorder.install(monkeypatch)

    tokens = await PkceLoopbackHandler(_config("google")).refresh(
        Tokens(access="a", refresh="1//r")
    )

    assert recorder.calls[0]["url"] == GOOGLE_BROKER.token_url
    assert tokens.extra["client_id"] == GOOGLE_ID
    assert tokens.extra["token_broker"] == "google"


@pytest.mark.asyncio
async def test_vanished_broker_asks_for_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder({"access_token": "x"})
    recorder.install(monkeypatch)
    grant = Tokens(access="a", refresh="r", extra={"client_id": "c", "token_broker": "gone"})
    with pytest.raises(RuntimeError, match="reconnect"):
        await PkceLoopbackHandler(_config(None)).refresh(grant)
    assert recorder.calls == []


# ---------------------------------------------------------------------------
# Slack: https redirect through the broker, loopback listener unchanged
# ---------------------------------------------------------------------------


class _FakeListener:
    """A loopback callback server that never binds a socket."""

    redirect_uri = "http://127.0.0.1:3118/oauth/callback"

    def __init__(self) -> None:
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False


@pytest.mark.asyncio
async def test_slack_authorize_uses_broker_callback_while_listening_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secrets(monkeypatch, {})
    listener = _FakeListener()
    seen: dict = {}

    def _make(state, **kwargs):  # noqa: ANN001, ANN003
        seen.update(kwargs)
        return listener

    monkeypatch.setattr("jarvis.marketplace.auth.oauth_pkce_loopback.make_callback_server", _make)
    handler = PkceLoopbackHandler(build_pkce_config("slack", _slack_auth()))

    session = await handler.start(None)

    assert listener.started is True
    assert seen["fixed_port"] == 3118
    assert seen["callback_path"] == "/oauth/callback"
    query = parse_qs(urlsplit(session.open_url).query)
    assert query["redirect_uri"] == [SLACK_BROKER.redirect_uri]
    assert query["client_id"] == [SLACK_ID]
    assert session.redirect_uri == SLACK_BROKER.redirect_uri
    pending = handler._pending[session.flow_id]  # noqa: SLF001
    assert pending.redirect_uri == SLACK_BROKER.redirect_uri
    await handler.cancel(session)


@pytest.mark.asyncio
async def test_slack_exchange_sends_the_broker_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(
        {"ok": True, "authed_user": {"access_token": "xoxp-1", "expires_in": 43200}}
    )
    recorder.install(monkeypatch)
    _secrets(monkeypatch, {})
    config = build_pkce_config("slack", _slack_auth())

    tokens = await PkceLoopbackHandler(config)._exchange(  # noqa: SLF001
        _pending(config, SLACK_BROKER.redirect_uri), code="slack-code"
    )

    (call,) = recorder.calls
    assert call["url"] == SLACK_BROKER.token_url
    assert call["data"]["redirect_uri"] == SLACK_BROKER.redirect_uri
    assert "client_secret" not in call["data"]
    assert tokens.extra["token_broker"] == "slack"


def test_google_keeps_the_loopback_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets(monkeypatch, {})
    handler = PkceLoopbackHandler(build_pkce_config("gmail", _google_auth()))
    assert handler._redirect_uri("http://127.0.0.1:3121") == "http://127.0.0.1:3121"  # noqa: SLF001
