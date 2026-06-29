"""Regression guard for the workspace-PTY "stuck on connecting" bug.

Root cause: the fast-boot desktop path mints a RAW ``secrets.token_urlsafe``,
injects it into ``window.__JARVIS_TOKEN``, but never issues it through
``GET /api/missions/auth/token`` — so it is absent from the in-memory token set
and every token-gated WebSocket (workspace PTY, missions PTY/stream) rejects it
with close code 4401. The frontend then hangs forever on "connecting".

Fix: the desktop session token is seeded into the token store at server build
via ``register_session_token_from_env`` so ``validate_token`` accepts it.
"""
from __future__ import annotations

from jarvis.ui.web.missions_auth import (
    issue_token,
    register_session_token_from_env,
    register_token,
    reset_tokens,
    validate_token,
)


def test_raw_unissued_token_is_rejected_until_registered() -> None:
    reset_tokens()
    raw = "raw-session-token-never-issued-0123456789"
    assert validate_token(raw) is False  # exactly the 4401 path

    register_token(raw)
    assert validate_token(raw) is True


def test_register_session_token_from_env_seeds_the_injected_token(monkeypatch) -> None:
    reset_tokens()
    env_var = "JARVIS_TEST_AUTH_TOKEN_ENV"
    injected = "injected-desktop-session-token-abc"
    monkeypatch.setenv(env_var, injected)

    # Mirrors the desktop: the token is injected but not issued → invalid.
    assert validate_token(injected) is False

    returned = register_session_token_from_env(env_var)
    assert returned == injected
    assert validate_token(injected) is True


def test_register_session_token_from_env_noops_when_unset(monkeypatch) -> None:
    reset_tokens()
    monkeypatch.delenv("JARVIS_TEST_AUTH_TOKEN_UNSET", raising=False)
    assert register_session_token_from_env("JARVIS_TEST_AUTH_TOKEN_UNSET") is None


def test_issued_tokens_still_validate() -> None:
    reset_tokens()
    tok = issue_token()
    assert validate_token(tok) is True
