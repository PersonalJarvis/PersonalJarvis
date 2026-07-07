"""The live Claude OAuth token reader must be EXPIRY-aware.

Forensic ground truth (2026-07-06, missions 019f36e5 + 019f38b1): the Claude
Max OAuth access token in ``~/.claude/.credentials.json`` expired at 02:53 and
nothing on the host refreshes it anymore (interactive Claude sessions run on a
different CLAUDE_CONFIG_DIR profile). ``read_live_claude_oauth_token`` returned
the DEAD token anyway (it never read ``expiresAt``), ``build_worker_env``
injected it as ``CLAUDE_CODE_OAUTH_TOKEN``, and the worker — pinned to an
isolated CLAUDE_CONFIG_DIR with no refresh token — failed every spawn with
"Failed to authenticate. API Error: 401 Invalid authentication credentials".

Contract under test: a token whose ``expiresAt`` lies in the past is treated
as ABSENT (never injected); a missing ``expiresAt`` stays fail-open so older
CLI credential shapes keep working.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.missions.isolation import env as env_mod

# Test fixtures, not real credentials.
_TEST_OAT = "sk-ant-oat01-test-token"  # noqa: S105 — test fixture, not a real token
_TEST_CLASSIC_KEY = "sk-ant-api03-classic-key"  # noqa: S105 — test fixture


def _write_credentials(
    tmp_path: Path,
    *,
    token: str = _TEST_OAT,
    expires_at_ms: float | None = None,
    omit_expiry: bool = False,
) -> Path:
    creds = tmp_path / ".credentials.json"
    oauth: dict[str, object] = {"accessToken": token}
    if not omit_expiry and expires_at_ms is not None:
        oauth["expiresAt"] = expires_at_ms
    creds.write_text(
        json.dumps({"claudeAiOauth": oauth}), encoding="utf-8"
    )
    return creds


def _pin_credentials_path(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(env_mod, "_claude_credentials_path", lambda: path)


NOW_S = 1_783_300_000.0  # arbitrary fixed wall clock for the tests


def test_valid_token_is_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    creds = _write_credentials(
        tmp_path, expires_at_ms=(NOW_S + 3600.0) * 1000.0
    )
    _pin_credentials_path(monkeypatch, creds)
    token = env_mod.read_live_claude_oauth_token(now_fn=lambda: NOW_S)
    assert token == _TEST_OAT
    assert env_mod.live_claude_oauth_status(now_fn=lambda: NOW_S) == "valid"


def test_expired_token_is_never_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 2026-07-06 shape: expiresAt hours in the past → treat as absent."""
    creds = _write_credentials(
        tmp_path, expires_at_ms=(NOW_S - 18 * 3600.0) * 1000.0
    )
    _pin_credentials_path(monkeypatch, creds)
    assert env_mod.read_live_claude_oauth_token(now_fn=lambda: NOW_S) is None
    assert env_mod.live_claude_oauth_status(now_fn=lambda: NOW_S) == "expired"


def test_token_expiring_within_slack_is_treated_as_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token that dies seconds from now would 401 mid-mission — skip it."""
    creds = _write_credentials(
        tmp_path, expires_at_ms=(NOW_S + 10.0) * 1000.0
    )
    _pin_credentials_path(monkeypatch, creds)
    assert env_mod.read_live_claude_oauth_token(now_fn=lambda: NOW_S) is None


def test_missing_expiry_field_stays_fail_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Older credential shapes without expiresAt keep working (back-compat)."""
    creds = _write_credentials(tmp_path, omit_expiry=True)
    _pin_credentials_path(monkeypatch, creds)
    token = env_mod.read_live_claude_oauth_token(now_fn=lambda: NOW_S)
    assert token == _TEST_OAT
    assert env_mod.live_claude_oauth_status(now_fn=lambda: NOW_S) == "valid"


def test_absent_file_reports_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_credentials_path(monkeypatch, tmp_path / "nope" / ".credentials.json")
    assert env_mod.read_live_claude_oauth_token(now_fn=lambda: NOW_S) is None
    assert env_mod.live_claude_oauth_status(now_fn=lambda: NOW_S) == "absent"


def test_non_oat_token_reports_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only sk-ant-oat bearers count — anything else is not a live OAuth login."""
    creds = _write_credentials(
        tmp_path, token=_TEST_CLASSIC_KEY,
        expires_at_ms=(NOW_S + 3600.0) * 1000.0,
    )
    _pin_credentials_path(monkeypatch, creds)
    assert env_mod.read_live_claude_oauth_token(now_fn=lambda: NOW_S) is None
    assert env_mod.live_claude_oauth_status(now_fn=lambda: NOW_S) == "absent"
