"""Unit tests for process-local authenticated session tokens."""
from __future__ import annotations

from jarvis.ui.web.missions_auth import (
    issue_token,
    register_token,
    reset_tokens,
    revoke_token,
    validate_token,
)


def test_raw_unissued_token_is_rejected_until_registered() -> None:
    reset_tokens()
    raw = "raw-session-token-never-issued-0123456789"
    assert validate_token(raw) is False

    register_token(raw)
    assert validate_token(raw) is True


def test_revoke_token_invalidates_registered_token() -> None:
    reset_tokens()
    raw = "registered-session-that-will-be-revoked"
    register_token(raw)
    revoke_token(raw)
    assert validate_token(raw) is False


def test_issued_tokens_still_validate() -> None:
    reset_tokens()
    tok = issue_token()
    assert validate_token(tok) is True
