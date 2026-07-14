"""The live Claude OAuth token reader must be EXPIRY- and multi-dir-aware.

Forensic ground truth (2026-07-06, missions 019f36e5 + 019f38b1): the Claude
Max OAuth access token in ``~/.claude/.credentials.json`` expired at 02:53 and
nothing on the host refreshes it anymore (interactive Claude sessions run on a
different CLAUDE_CONFIG_DIR profile). ``read_live_claude_oauth_token`` returned
the DEAD token anyway (it never read ``expiresAt``), ``build_worker_env``
injected it as ``CLAUDE_CODE_OAUTH_TOKEN``, and the worker — pinned to an
isolated CLAUDE_CONFIG_DIR with no refresh token — failed every spawn with
"Failed to authenticate. API Error: 401 Invalid authentication credentials".

Second half of the same class (2026-07-10): the freshly-refreshed login that
DID exist sat in the profile-manager config dir the readers never looked at,
so the app declared the Claude worker dead while a live login was on disk.

Contract under test: a token whose ``expiresAt`` lies in the past is treated
as ABSENT (never injected); a missing ``expiresAt`` stays fail-open so older
CLI credential shapes keep working; and the freshest live login across ALL
candidate config dirs wins over a stale default-dir copy.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis import claude_credentials
from jarvis.missions.isolation import env as env_mod

# Test fixtures, not real credentials.
_TEST_OAT = "sk-ant-oat01-test-token"  # noqa: S105 — test fixture, not a real token
_TEST_OAT_FRESH = "sk-ant-oat01-fresh-token"  # noqa: S105 — test fixture
_TEST_CLASSIC_KEY = "sk-ant-api03-classic-key"  # noqa: S105 — test fixture


def _write_credentials(
    config_dir: Path,
    *,
    token: str = _TEST_OAT,
    expires_at_ms: float | None = None,
    omit_expiry: bool = False,
) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    creds = config_dir / ".credentials.json"
    oauth: dict[str, object] = {"accessToken": token}
    if not omit_expiry and expires_at_ms is not None:
        oauth["expiresAt"] = expires_at_ms
    creds.write_text(
        json.dumps({"claudeAiOauth": oauth}), encoding="utf-8"
    )
    return creds


def _pin_config_dirs(monkeypatch: pytest.MonkeyPatch, dirs: list[Path]) -> None:
    """Pin the candidate config dirs the readers scan (hermetic tests)."""
    monkeypatch.setattr(claude_credentials, "claude_config_dirs", lambda: dirs)


NOW_S = 1_783_300_000.0  # arbitrary fixed wall clock for the tests


def test_valid_token_is_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_credentials(tmp_path, expires_at_ms=(NOW_S + 3600.0) * 1000.0)
    _pin_config_dirs(monkeypatch, [tmp_path])
    token = env_mod.read_live_claude_oauth_token(now_fn=lambda: NOW_S)
    assert token == _TEST_OAT
    assert env_mod.live_claude_oauth_status(now_fn=lambda: NOW_S) == "valid"


def test_expired_token_is_never_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 2026-07-06 shape: expiresAt hours in the past → treat as absent."""
    _write_credentials(tmp_path, expires_at_ms=(NOW_S - 18 * 3600.0) * 1000.0)
    _pin_config_dirs(monkeypatch, [tmp_path])
    assert env_mod.read_live_claude_oauth_token(now_fn=lambda: NOW_S) is None
    assert env_mod.live_claude_oauth_status(now_fn=lambda: NOW_S) == "expired"


def test_token_expiring_within_slack_is_treated_as_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token that dies seconds from now would 401 mid-mission — skip it."""
    _write_credentials(tmp_path, expires_at_ms=(NOW_S + 10.0) * 1000.0)
    _pin_config_dirs(monkeypatch, [tmp_path])
    assert env_mod.read_live_claude_oauth_token(now_fn=lambda: NOW_S) is None


def test_missing_expiry_field_stays_fail_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Older credential shapes without expiresAt keep working (back-compat)."""
    _write_credentials(tmp_path, omit_expiry=True)
    _pin_config_dirs(monkeypatch, [tmp_path])
    token = env_mod.read_live_claude_oauth_token(now_fn=lambda: NOW_S)
    assert token == _TEST_OAT
    assert env_mod.live_claude_oauth_status(now_fn=lambda: NOW_S) == "valid"


def test_absent_file_reports_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_config_dirs(monkeypatch, [tmp_path / "nope"])
    assert env_mod.read_live_claude_oauth_token(now_fn=lambda: NOW_S) is None
    assert env_mod.live_claude_oauth_status(now_fn=lambda: NOW_S) == "absent"


def test_non_oat_token_reports_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only sk-ant-oat bearers count — anything else is not a live OAuth login."""
    _write_credentials(
        tmp_path, token=_TEST_CLASSIC_KEY,
        expires_at_ms=(NOW_S + 3600.0) * 1000.0,
    )
    _pin_config_dirs(monkeypatch, [tmp_path])
    assert env_mod.read_live_claude_oauth_token(now_fn=lambda: NOW_S) is None
    assert env_mod.live_claude_oauth_status(now_fn=lambda: NOW_S) == "absent"


def test_fresh_profile_login_wins_over_stale_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 2026-07-10 shape: ~/.claude expired in place while the profile
    manager's config dir holds the real, freshly-refreshed login — the live
    token must be injected instead of declaring the Claude worker dead."""
    default_dir = tmp_path / "dot-claude"
    profile_dir = tmp_path / "profile"
    _write_credentials(default_dir, expires_at_ms=(NOW_S - 48 * 3600.0) * 1000.0)
    _write_credentials(
        profile_dir,
        token=_TEST_OAT_FRESH,
        expires_at_ms=(NOW_S + 4 * 3600.0) * 1000.0,
    )
    _pin_config_dirs(monkeypatch, [default_dir, profile_dir])
    token = env_mod.read_live_claude_oauth_token(now_fn=lambda: NOW_S)
    assert token == _TEST_OAT_FRESH
    assert env_mod.live_claude_oauth_status(now_fn=lambda: NOW_S) == "valid"
