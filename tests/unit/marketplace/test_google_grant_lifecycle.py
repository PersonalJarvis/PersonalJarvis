"""Google grant lifecycle regressions.

Gmail, Drive, Calendar, YouTube Music, YouTube Studio and Google Cloud share
one Google OAuth client. Three defects made those connections die or lie:

* Google's revocation is project-wide: revoking one plugin's token on
  disconnect ended the grant of every other Google plugin.
* Google answers a deleted or disabled OAuth client with ``deleted_client`` /
  ``disabled_client``. Those codes were not recognized, so the refresh was
  retried as "transient" forever and the card never offered Reconnect.
* A grant Google caps in time (``refresh_token_expires_in``, sent while the
  OAuth app is in Testing status) was not recorded, so the 7-day death came
  without warning.
"""

from __future__ import annotations

import httpx
import pytest

from jarvis.marketplace.auth.base import sanitize_provider_error
from jarvis.marketplace.auth.oauth_pkce_loopback import (
    PkceLoopbackConfig,
    PkceLoopbackHandler,
    _PendingPkceFlow,
)
from jarvis.marketplace.catalog import PluginSpec
from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.refresh_scheduler import REVOKED, reauth_reason_for, refresh_plugin_token
from jarvis.marketplace.revoke import (
    REVOCATION_SHARED,
    revoke_unless_shared,
    revokes_whole_project,
)
from jarvis.marketplace.token_store import (
    REAUTH_CLIENT_REJECTED,
    InMemoryBackend,
    Tokens,
    TokenStore,
)

GOOGLE_CLIENT = "123456789012-abc.apps.googleusercontent.com"
OTHER_PROJECT_CLIENT = "999999999999-xyz.apps.googleusercontent.com"
TEST_CLIENT_SECRET = "unit-test-client-secret"  # noqa: S105


def _google_tokens(client_id: str = GOOGLE_CLIENT, refresh: str = "rt") -> Tokens:
    return Tokens(access="at", refresh=refresh, extra={"client_id": client_id})


def _recording_transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    return httpx.MockTransport(handler)


def _gmail() -> PluginSpec:
    spec = load_catalog().by_id("gmail")
    assert spec is not None
    return spec


# --------------------------------------------------------------------------
# Project-wide revocation
# --------------------------------------------------------------------------


def test_google_revocation_endpoints_are_project_wide() -> None:
    assert revokes_whole_project("https://oauth2.googleapis.com/revoke")
    assert revokes_whole_project("https://accounts.google.com/o/oauth2/revoke")
    assert not revokes_whole_project("https://slack.com/api/auth.revoke")


@pytest.mark.asyncio
async def test_disconnecting_one_google_plugin_keeps_the_shared_grant() -> None:
    """Revoking Gmail's token would end Drive's grant too, so it is skipped."""
    store = TokenStore(InMemoryBackend())
    store.save("gmail", _google_tokens(refresh="gmail-rt"))
    store.save("google_drive", _google_tokens(refresh="drive-rt"))
    seen: list[httpx.Request] = []

    outcome = await revoke_unless_shared(
        _gmail(), store.load("gmail"), store, transport=_recording_transport(seen)
    )

    assert outcome == REVOCATION_SHARED
    assert seen == [], "no revocation request may reach Google"


@pytest.mark.asyncio
async def test_the_last_google_plugin_revokes_the_grant() -> None:
    store = TokenStore(InMemoryBackend())
    store.save("gmail", _google_tokens())
    seen: list[httpx.Request] = []

    outcome = await revoke_unless_shared(
        _gmail(), store.load("gmail"), store, transport=_recording_transport(seen)
    )

    assert outcome == "revoked"
    assert len(seen) == 1
    assert "token=rt" in seen[0].content.decode()


@pytest.mark.asyncio
async def test_a_flagged_sibling_still_protects_the_grant() -> None:
    """A reconnect flag can be wrong and self-heals; never destroy its grant."""
    store = TokenStore(InMemoryBackend())
    store.save("gmail", _google_tokens())
    store.save(
        "google_calendar",
        Tokens(
            access="at",
            refresh="cal-rt",
            extra={"client_id": GOOGLE_CLIENT},
            needs_reauth=True,
        ),
    )
    seen: list[httpx.Request] = []

    outcome = await revoke_unless_shared(
        _gmail(), store.load("gmail"), store, transport=_recording_transport(seen)
    )

    assert outcome == REVOCATION_SHARED
    assert seen == []


@pytest.mark.asyncio
async def test_a_sibling_from_another_google_project_does_not_block_revocation() -> None:
    store = TokenStore(InMemoryBackend())
    store.save("gmail", _google_tokens())
    store.save("google_drive", _google_tokens(client_id=OTHER_PROJECT_CLIENT))
    seen: list[httpx.Request] = []

    outcome = await revoke_unless_shared(
        _gmail(), store.load("gmail"), store, transport=_recording_transport(seen)
    )

    assert outcome == "revoked"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_a_legacy_sibling_without_a_bound_client_is_matched_by_family() -> None:
    """Grants stored before the client id was persisted share by family."""
    store = TokenStore(InMemoryBackend())
    store.save("gmail", _google_tokens())
    store.save("google_drive", Tokens(access="at", refresh="legacy-rt"))
    seen: list[httpx.Request] = []

    outcome = await revoke_unless_shared(
        _gmail(), store.load("gmail"), store, transport=_recording_transport(seen)
    )

    assert outcome == REVOCATION_SHARED
    assert seen == []


@pytest.mark.asyncio
async def test_a_per_token_provider_still_revokes_with_siblings() -> None:
    """Only project-wide revocation is guarded; other providers revoke one token."""
    spec = PluginSpec.model_validate(
        {
            "id": "gmail",
            "display_name": "Demo",
            "description": "d",
            "category": "Developer",
            "logo_slug": "demo",
            "auth": {
                "mode": "oauth_pkce_loopback",
                "authorization_url": "https://example.test/authorize",
                "token_url": "https://example.test/token",
                "revocation_url": "https://example.test/revoke",
                "client_id": "cid",
                "scopes": ["read"],
            },
        }
    )
    store = TokenStore(InMemoryBackend())
    store.save("gmail", Tokens(access="at", refresh="rt", extra={"client_id": "cid"}))
    store.save("google_drive", Tokens(access="at", refresh="rt2", extra={"client_id": "cid"}))
    seen: list[httpx.Request] = []

    outcome = await revoke_unless_shared(
        spec, store.load("gmail"), store, transport=_recording_transport(seen)
    )

    assert outcome == "revoked"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_disconnect_route_keeps_the_other_google_plugins_connected(monkeypatch) -> None:
    from starlette.requests import Request

    from jarvis.ui.web import marketplace_routes

    store = TokenStore(InMemoryBackend())
    store.save("gmail", _google_tokens(refresh="gmail-rt"))
    store.save("google_drive", _google_tokens(refresh="drive-rt"))
    calls: list[str] = []

    async def _no_network(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("revoke")
        return "revoked"

    monkeypatch.setattr(marketplace_routes, "TokenStore", lambda: store)
    monkeypatch.setattr(marketplace_routes, "_refresh_plugin_in_live_registry", lambda _p: None)
    monkeypatch.setattr("jarvis.marketplace.revoke.revoke_tokens", _no_network)

    request = Request({"type": "http", "app": None, "headers": []})
    body = await marketplace_routes.disconnect("gmail", request)

    assert body["revocation"] == REVOCATION_SHARED
    assert calls == []
    assert store.load("gmail") is None
    assert store.load("google_drive") is not None


# --------------------------------------------------------------------------
# Deleted / disabled OAuth client
# --------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["deleted_client", "disabled_client"])
def test_deleted_or_disabled_client_is_a_client_rejection(code: str) -> None:
    body = '{"error": "' + code + '"}'
    message = "refresh HTTP 401: " + sanitize_provider_error(body)
    assert reauth_reason_for(message) == REAUTH_CLIENT_REJECTED
    assert code not in sanitize_provider_error(code + " The OAuth client was deleted.")


@pytest.mark.asyncio
async def test_refresh_against_a_deleted_client_flags_reconnect(monkeypatch) -> None:
    async def _fake_post(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return httpx.Response(
            401,
            json={"error": "deleted_client", "error_description": "The OAuth client was deleted."},
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    cfg = PkceLoopbackConfig(
        plugin_id="gmail",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",  # noqa: S106
        client_id=GOOGLE_CLIENT,
        client_secret=TEST_CLIENT_SECRET,
        callback_port=0,
        scopes=["https://mail.google.com/"],
    )
    store = TokenStore(InMemoryBackend())
    store.save("gmail", _google_tokens())

    attempt = await refresh_plugin_token(
        "gmail", store, lambda _pid: PkceLoopbackHandler(cfg), force=True
    )

    assert attempt.outcome == REVOKED
    saved = store.load("gmail")
    assert saved.needs_reauth
    assert saved.reauth_reason == REAUTH_CLIENT_REJECTED


# --------------------------------------------------------------------------
# Time-limited grants (Testing status)
# --------------------------------------------------------------------------


def _google_cfg() -> PkceLoopbackConfig:
    return PkceLoopbackConfig(
        plugin_id="google_calendar",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",  # noqa: S106
        client_id=GOOGLE_CLIENT,
        client_secret=TEST_CLIENT_SECRET,
        callback_port=0,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )


@pytest.mark.asyncio
async def test_exchange_records_when_google_ends_a_time_limited_grant(monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    async def _fake_post(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3599,
                "refresh_token_expires_in": 604799,
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    cfg = _google_cfg()
    pending = _PendingPkceFlow(
        config=cfg, callback_server=None, code_verifier="v", redirect_uri="http://127.0.0.1:3122"
    )

    tokens = await PkceLoopbackHandler(cfg)._exchange(pending, code="code")  # noqa: SLF001

    ends = datetime.fromisoformat(tokens.extra["refresh_expires_at"])
    assert timedelta(days=6, hours=23) < ends - datetime.now(UTC) <= timedelta(days=7)


@pytest.mark.asyncio
async def test_a_production_grant_records_no_end(monkeypatch) -> None:
    async def _fake_post(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return httpx.Response(
            200, json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3599}
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    cfg = _google_cfg()
    pending = _PendingPkceFlow(
        config=cfg, callback_server=None, code_verifier="v", redirect_uri="http://127.0.0.1:3122"
    )

    tokens = await PkceLoopbackHandler(cfg)._exchange(pending, code="code")  # noqa: SLF001

    assert "refresh_expires_at" not in tokens.extra


@pytest.mark.asyncio
async def test_refresh_updates_the_recorded_grant_end(monkeypatch) -> None:
    async def _fake_post(self, url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return httpx.Response(
            200,
            json={"access_token": "new", "expires_in": 3599, "refresh_token_expires_in": 3600},
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    refreshed = await PkceLoopbackHandler(_google_cfg()).refresh(_google_tokens())

    assert refreshed.access == "new"
    assert "refresh_expires_at" in refreshed.extra


@pytest.mark.asyncio
async def test_list_plugins_serves_the_grant_end(monkeypatch) -> None:
    from fastapi import Response

    from jarvis.ui.web import marketplace_routes

    store = TokenStore(InMemoryBackend())
    store.save(
        "gmail",
        Tokens(access="at", extra={"refresh_expires_at": "2026-10-09T12:00:00+00:00"}),
    )
    monkeypatch.setattr(marketplace_routes, "TokenStore", lambda: store)

    payload = await marketplace_routes.list_plugins(Response())
    gmail = next(p for p in payload["plugins"] if p["id"] == "gmail")
    drive = next(p for p in payload["plugins"] if p["id"] == "google_drive")

    assert gmail["refresh_expires_at"] == "2026-10-09T12:00:00+00:00"
    assert drive["refresh_expires_at"] is None
