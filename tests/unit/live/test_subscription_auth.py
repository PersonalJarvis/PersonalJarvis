"""Subscription credentials cannot inherit API billing or overwrite another login."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from jarvis.core.http_pool import SyncHttpClientPool
from jarvis.live.subscription_auth import SubscriptionAuth, SubscriptionAuthError


def _token(expiry: float, account_id: str = "test-account") -> str:
    payload = {"exp": expiry, "https://api.openai.com/auth": {"chatgpt_account_id": account_id}}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"fixture.{encoded}.signature"


def _account(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        id="codex:test", label="Test account", platform="codex", config_dir=tmp_path
    )


def _write(tmp_path: Path, *, expired: bool = False, **extra: object) -> dict:
    value = {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": _token(time.time() + (-100 if expired else 3600)),
            "refresh_token": "fixture-refresh-token",
            "account_id": "test-account",
        },
        **extra,
    }
    (tmp_path / "auth.json").write_text(json.dumps(value), encoding="utf-8")
    return value


@pytest.mark.asyncio
async def test_local_status_never_refreshes_or_exposes_credentials(tmp_path):
    _write(tmp_path, expired=True)

    def network_forbidden(_request):
        pytest.fail("Readiness must not contact a provider")

    pool = SyncHttpClientPool(timeout_s=1, transport=httpx.MockTransport(network_forbidden))
    auth = SubscriptionAuth(account=_account(tmp_path), http_pool=pool)
    status = await auth.status()
    assert status["connected"] is True
    assert status["voice_verified"] is False
    assert status["account_id"] == "codex:test"
    assert "fixture" not in json.dumps(status)
    await auth.aclose()


@pytest.mark.asyncio
async def test_api_only_and_explicit_api_mode_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-ambient-api-key")
    for value in ({"OPENAI_API_KEY": "fixture-api-key"}, _write(tmp_path, auth_mode="api_key")):
        (tmp_path / "auth.json").write_text(json.dumps(value), encoding="utf-8")
        auth = SubscriptionAuth(account=_account(tmp_path))
        assert not (await auth.status())["connected"]
        with pytest.raises(SubscriptionAuthError):
            await auth.credentials()
        await auth.aclose()


@pytest.mark.asyncio
async def test_expiring_oauth_refresh_is_serialized_and_persisted(tmp_path):
    _write(tmp_path, expired=True, unrelated={"keep": True})
    calls = []
    fresh = _token(time.time() + 7200)

    def refresh(request):
        calls.append(request)
        assert str(request.url) == "https://auth.openai.com/oauth/token"
        assert json.loads(request.content)["grant_type"] == "refresh_token"
        return httpx.Response(200, json={"access_token": fresh, "refresh_token": "rotated-fixture"})

    pool = SyncHttpClientPool(timeout_s=1, transport=httpx.MockTransport(refresh))
    auth = SubscriptionAuth(account=_account(tmp_path), http_pool=pool)
    credentials = await asyncio.gather(auth.credentials(), auth.credentials())
    assert len(calls) == 1
    assert all(credential.access_token == fresh for credential in credentials)
    saved = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    assert saved["tokens"]["refresh_token"] == "rotated-fixture"  # noqa: S105 - fake token
    assert saved["unrelated"] == {"keep": True}
    assert fresh not in repr(credentials[0])
    assert "test-account" not in repr(credentials[0])
    await auth.aclose()


@pytest.mark.asyncio
async def test_terminal_refresh_failure_has_no_body_and_is_not_repeated(tmp_path):
    _write(tmp_path, expired=True)
    calls = []

    def refresh(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "sensitive-provider-body"})

    pool = SyncHttpClientPool(timeout_s=1, transport=httpx.MockTransport(refresh))
    auth = SubscriptionAuth(account=_account(tmp_path), http_pool=pool)
    for _ in range(2):
        with pytest.raises(SubscriptionAuthError) as caught:
            await auth.credentials()
        assert "sensitive-provider-body" not in str(caught.value)
    assert len(calls) == 1
    assert not (await auth.status())["connected"]
    await auth.aclose()


@pytest.mark.asyncio
async def test_concurrent_external_token_rotation_is_not_overwritten(tmp_path):
    _write(tmp_path, expired=True)
    external = _token(time.time() + 6000)

    def refresh(_request):
        changed = _write(tmp_path, owner="external-codex")
        changed["tokens"]["access_token"] = external
        (tmp_path / "auth.json").write_text(json.dumps(changed), encoding="utf-8")
        return httpx.Response(200, json={"access_token": _token(time.time() + 5000)})

    pool = SyncHttpClientPool(timeout_s=1, transport=httpx.MockTransport(refresh))
    auth = SubscriptionAuth(account=_account(tmp_path), http_pool=pool)
    assert (await auth.credentials()).access_token == external
    assert json.loads((tmp_path / "auth.json").read_text())["owner"] == "external-codex"
    await auth.aclose()


@pytest.mark.asyncio
async def test_explicit_missing_account_does_not_use_active_login(monkeypatch):
    from jarvis import agent_accounts

    monkeypatch.setattr(agent_accounts, "resolve", lambda _account_id: None)

    def active_forbidden(_platform):
        pytest.fail("An explicit deleted selection must not use another account")

    monkeypatch.setattr(agent_accounts, "active_account", active_forbidden)
    auth = SubscriptionAuth("codex:deleted")
    assert not (await auth.status())["connected"]
    with pytest.raises(SubscriptionAuthError):
        await auth.credentials()


@pytest.mark.asyncio
async def test_account_identity_can_come_from_oauth_claim(tmp_path):
    data = _write(tmp_path)
    data["tokens"].pop("account_id")
    (tmp_path / "auth.json").write_text(json.dumps(data), encoding="utf-8")
    auth = SubscriptionAuth(account=_account(tmp_path))
    assert (await auth.credentials()).account_id == "test-account"
    await auth.aclose()


@pytest.mark.asyncio
async def test_account_switch_in_saved_slot_is_rejected_for_active_call(tmp_path):
    _write(tmp_path)
    auth = SubscriptionAuth(account=_account(tmp_path))
    await auth.credentials()
    changed = _write(tmp_path)
    changed["tokens"]["account_id"] = "other-account"
    (tmp_path / "auth.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(SubscriptionAuthError, match="account changed"):
        await auth.credentials(force_refresh=True)
    await auth.aclose()


@pytest.mark.asyncio
async def test_refresh_cannot_switch_identity_before_saving(tmp_path):
    before = _write(tmp_path, expired=True)
    pool = SyncHttpClientPool(
        timeout_s=1,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json={"access_token": _token(time.time() + 3600, "other-account")}
            )
        ),
    )
    auth = SubscriptionAuth(account=_account(tmp_path), http_pool=pool)
    with pytest.raises(SubscriptionAuthError, match="different account"):
        await auth.credentials()
    assert json.loads((tmp_path / "auth.json").read_text()) == before
    await auth.aclose()


@pytest.mark.asyncio
async def test_two_reactive_refreshes_share_the_rotated_token(tmp_path):
    _write(tmp_path)
    calls = []

    def refresh(request):
        calls.append(request)
        return httpx.Response(200, json={"access_token": _token(time.time() + 3600)})

    pool = SyncHttpClientPool(timeout_s=1, transport=httpx.MockTransport(refresh))
    auth = SubscriptionAuth(account=_account(tmp_path), http_pool=pool)
    await auth.credentials()
    await asyncio.gather(auth.credentials(force_refresh=True), auth.credentials(force_refresh=True))
    assert len(calls) == 1
    await auth.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_close", [False, True])
async def test_cancelled_refresh_persists_before_http_pool_closes(tmp_path, cancel_close):
    _write(tmp_path, expired=True)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    fresh = _token(time.time() + 3600)
    closed = []

    def refresh(_request):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(timeout=5), "The test did not release its fake refresh"
        return httpx.Response(200, json={"access_token": fresh})

    class RecordingPool:
        def __init__(self):
            self.pool = SyncHttpClientPool(timeout_s=1, transport=httpx.MockTransport(refresh))

        def client(self):
            return self.pool.client()

        def close(self):
            saved = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
            closed.append(saved["tokens"]["access_token"])
            self.pool.close()

    auth = SubscriptionAuth(account=_account(tmp_path), http_pool=RecordingPool())
    caller = asyncio.create_task(auth.credentials())
    closing = None
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        closing = asyncio.create_task(auth.aclose())
        await asyncio.sleep(0)
        with pytest.raises(SubscriptionAuthError, match="closing"):
            await auth.credentials()
        assert not closing.done()
        assert not closed
        if cancel_close:
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
        release.set()
        await asyncio.wait_for(auth.aclose(), timeout=2)
        if not cancel_close:
            await closing
        assert closed == [fresh]
    finally:
        release.set()
        await auth.aclose()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)


@pytest.mark.asyncio
async def test_close_budget_leaves_refresh_pool_open_until_persistence(tmp_path, monkeypatch):
    from jarvis.live import subscription_auth

    monkeypatch.setattr(subscription_auth, "_AUTH_CLOSE_WAIT_S", 0.01)
    _write(tmp_path, expired=True)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    finished = asyncio.Event()
    release = threading.Event()
    fresh = _token(time.time() + 3600)
    closed = []

    def refresh(_request):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(timeout=5), "The test did not release its fake refresh"
        return httpx.Response(200, json={"access_token": fresh})

    class RecordingPool:
        def __init__(self):
            self.pool = SyncHttpClientPool(timeout_s=1, transport=httpx.MockTransport(refresh))

        def client(self):
            return self.pool.client()

        def close(self):
            saved = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
            closed.append(saved["tokens"]["access_token"])
            self.pool.close()
            loop.call_soon_threadsafe(finished.set)

    auth = SubscriptionAuth(account=_account(tmp_path), http_pool=RecordingPool())
    caller = asyncio.create_task(auth.credentials())
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        await asyncio.wait_for(auth.aclose(), timeout=1)
        assert not closed
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=2)
        assert closed == [fresh]
    finally:
        release.set()
        await auth.aclose()
        await asyncio.wait_for(finished.wait(), timeout=2)
