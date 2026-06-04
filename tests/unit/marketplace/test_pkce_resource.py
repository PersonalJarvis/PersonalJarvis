"""PKCE-loopback authorize/token params for provider-specific extensions."""

import httpx
import pytest

from jarvis.marketplace.auth.oauth_pkce_loopback import (
    PkceLoopbackConfig,
    PkceLoopbackHandler,
    _PendingPkceFlow,
)
from jarvis.marketplace.token_store import Tokens

TEST_CLIENT_SECRET = "unit-test-client-secret"  # noqa: S105
OLD_REFRESH_TOKEN = "old-refresh"  # noqa: S105


def _params(**overrides):
    scopes = overrides.pop("scopes", ["default"])
    cfg = PkceLoopbackConfig(
        plugin_id="asana",
        authorization_url="https://app.asana.com/-/oauth_authorize",
        token_url="https://app.asana.com/-/oauth_token",  # noqa: S106
        client_id="cid",
        callback_port=0,
        scopes=scopes,
        **overrides,
    )
    h = PkceLoopbackHandler(cfg)
    return h._authorize_params(
        redirect_uri="http://127.0.0.1:5/cb", state="s", challenge="c"
    )


def test_resource_param_added_when_set():
    assert _params(resource="https://mcp.asana.com/v2")["resource"] == (
        "https://mcp.asana.com/v2"
    )


def test_no_resource_param_when_unset():
    assert "resource" not in _params()


def test_offline_access_adds_access_type_and_prompt():
    p = _params(offline_access=True)
    assert p["access_type"] == "offline"
    assert p["prompt"] == "consent"


def test_no_offline_params_by_default():
    p = _params()
    assert "access_type" not in p
    assert "prompt" not in p


def test_scope_separator_space_joins_google_scopes():
    p = _params(
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ],
        scope_separator="space",
    )
    assert p["scope"] == (
        "https://www.googleapis.com/auth/gmail.readonly "
        "https://www.googleapis.com/auth/gmail.send"
    )


def test_scope_separator_defaults_to_comma_for_existing_pkce_plugins():
    p = _params(scopes=["chat:write", "users:read"])
    assert p["scope"] == "chat:write,users:read"


def test_core_pkce_params_always_present():
    p = _params()
    assert p["response_type"] == "code"
    assert p["client_id"] == "cid"
    assert p["code_challenge"] == "c"
    assert p["code_challenge_method"] == "S256"


@pytest.mark.asyncio
async def test_exchange_includes_client_secret_when_configured(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    async def _fake_post(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured["url"] = url
        captured["data"] = kwargs["data"]
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    cfg = PkceLoopbackConfig(
        plugin_id="google_drive",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",  # noqa: S106
        client_id="cid",
        client_secret=TEST_CLIENT_SECRET,
        callback_port=0,
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    pending = _PendingPkceFlow(
        config=cfg,
        callback_server=None,
        code_verifier="verifier",
        redirect_uri="http://127.0.0.1:3120",
    )

    tokens = await PkceLoopbackHandler(cfg)._exchange(pending, code="code")  # noqa: SLF001

    assert captured["url"] == "https://oauth2.googleapis.com/token"
    assert captured["data"]["client_id"] == "cid"
    assert captured["data"]["client_secret"] == TEST_CLIENT_SECRET
    assert captured["data"]["code_verifier"] == "verifier"
    assert tokens.refresh == "refresh"


@pytest.mark.asyncio
async def test_refresh_includes_client_secret_when_configured(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict = {}

    async def _fake_post(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured["url"] = url
        captured["data"] = kwargs["data"]
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "expires_in": 3600,
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    cfg = PkceLoopbackConfig(
        plugin_id="google_drive",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",  # noqa: S106
        client_id="cid",
        client_secret=TEST_CLIENT_SECRET,
        callback_port=0,
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )

    tokens = await PkceLoopbackHandler(cfg).refresh(
        Tokens(access="old-access", refresh=OLD_REFRESH_TOKEN)
    )

    assert captured["url"] == "https://oauth2.googleapis.com/token"
    assert captured["data"]["client_id"] == "cid"
    assert captured["data"]["client_secret"] == TEST_CLIENT_SECRET
    assert captured["data"]["refresh_token"] == OLD_REFRESH_TOKEN
    assert tokens.refresh == OLD_REFRESH_TOKEN
