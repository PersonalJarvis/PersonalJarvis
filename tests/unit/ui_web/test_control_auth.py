"""Auth for the Jarvis Control API (step 5).

``require_control_key`` guards every /api/control/* route with a Bearer key.
``require_control_key_or_session`` additionally accepts an authenticated UI
session so the desktop Settings panel can fetch or rotate the key.
``assert_bind_safe`` is the fail-closed boot check: never expose a non-loopback
bind without a key (the key, not the bind address, is the boundary).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from jarvis.core import control_key as ck
from jarvis.ui.web import control_auth as ca


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    def __init__(
        self,
        *,
        auth: str | None = None,
        host: str = "testclient",
        session_token: str | None = None,
    ) -> None:
        self.headers: dict[str, str] = {}
        if auth is not None:
            self.headers["authorization"] = auth
        self.client = _FakeClient(host)
        self.cookies = {}
        if session_token is not None:
            self.cookies[ca.COOKIE_NAME] = session_token


@pytest.fixture
def stored_key(monkeypatch):
    monkeypatch.setattr(ck, "get_control_key", lambda: "jctl_realkey")
    return "jctl_realkey"


# --- require_control_key (Bearer only) ---


async def test_rejects_missing_header(stored_key) -> None:
    with pytest.raises(HTTPException) as exc:
        await ca.require_control_key(_FakeRequest())
    assert exc.value.status_code == 401


async def test_rejects_wrong_key(stored_key) -> None:
    with pytest.raises(HTTPException) as exc:
        await ca.require_control_key(_FakeRequest(auth="Bearer jctl_wrong"))
    assert exc.value.status_code == 401


async def test_accepts_valid_bearer(stored_key) -> None:
    # Must not raise.
    await ca.require_control_key(_FakeRequest(auth="Bearer jctl_realkey"))


async def test_loopback_does_not_bypass_main_guard(stored_key) -> None:
    # A loopback caller still needs the key on the main endpoints — otherwise
    # the key would be pointless for local agents on desktop.
    with pytest.raises(HTTPException):
        await ca.require_control_key(_FakeRequest(host="127.0.0.1"))


# --- require_control_key_or_session (key-reveal / rotate) ---


async def test_raw_loopback_does_not_bypass_session_guard(stored_key) -> None:
    with pytest.raises(HTTPException):
        await ca.require_control_key_or_session(_FakeRequest(host="127.0.0.1"))


async def test_session_guard_denies_remote_without_key(stored_key) -> None:
    with pytest.raises(HTTPException):
        await ca.require_control_key_or_session(_FakeRequest(host="203.0.113.7"))


async def test_session_guard_allows_remote_with_valid_key(stored_key) -> None:
    await ca.require_control_key_or_session(
        _FakeRequest(auth="Bearer jctl_realkey", host="203.0.113.7")
    )


async def test_session_guard_accepts_registered_ui_session(stored_key) -> None:
    from jarvis.ui.web.missions_auth import register_token, revoke_token

    token = "ui-session-token"  # noqa: S105
    register_token(token)
    try:
        await ca.require_control_key_or_session(_FakeRequest(session_token=token))
    finally:
        revoke_token(token)


# --- assert_bind_safe (fail-closed) ---


def test_bind_safe_allows_loopback_without_key() -> None:
    ca.assert_bind_safe("127.0.0.1", None)  # no raise


def test_bind_safe_allows_non_loopback_with_key() -> None:
    ca.assert_bind_safe("0.0.0.0", "jctl_realkey")  # noqa: S104 — test input, no real bind


def test_bind_safe_refuses_non_loopback_without_key() -> None:
    with pytest.raises(RuntimeError):
        ca.assert_bind_safe("0.0.0.0", None)  # noqa: S104 — test input, no real bind
