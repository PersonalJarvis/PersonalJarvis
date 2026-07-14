"""The shared Claude credentials locator must let the FRESHEST login win.

Forensic ground truth (2026-07-10): every interactive Claude session on the
maintainer host runs through a profile manager that pins ``CLAUDE_CONFIG_DIR``
to a per-profile directory, so ``~/.claude/.credentials.json`` expired in
place on 07-08 while a freshly-refreshed login sat in the profile dir. Both
hardcoded ``~/.claude`` readers (mission-worker token injection and the
subscription card) reported "expired", the Jarvis-Agents banner declared the
worker unavailable, and every mission diverted to codex — although a live
login existed on disk the whole time.

Contract under test: ``freshest_claude_oauth`` scans every candidate config
dir and returns the live login with the farthest expiry; only when NO
candidate is live does it report expired/absent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis import claude_credentials as cc

# Test fixtures, not real credentials.
_TEST_OAT = "sk-ant-oat01-test-token"  # noqa: S105 — test fixture, not a real token
_TEST_OAT_2 = "sk-ant-oat01-second-token"  # noqa: S105 — test fixture
_TEST_CLASSIC_KEY = "sk-ant-api03-classic-key"  # noqa: S105 — test fixture

NOW_S = 1_783_300_000.0  # arbitrary fixed wall clock for the tests


def _write_credentials(
    config_dir: Path,
    *,
    token: str = _TEST_OAT,
    expires_at_ms: float | None = None,
    subscription_type: str | None = None,
) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    oauth: dict[str, object] = {"accessToken": token}
    if expires_at_ms is not None:
        oauth["expiresAt"] = expires_at_ms
    if subscription_type is not None:
        oauth["subscriptionType"] = subscription_type
    creds = config_dir / ".credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": oauth}), encoding="utf-8")
    return creds


def _pin_dirs(monkeypatch: pytest.MonkeyPatch, dirs: list[Path]) -> None:
    monkeypatch.setattr(cc, "claude_config_dirs", lambda: dirs)


# -- freshest_claude_oauth ----------------------------------------------


def test_single_valid_login_is_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = tmp_path / "default"
    _write_credentials(
        d, expires_at_ms=(NOW_S + 3600.0) * 1000.0, subscription_type="max"
    )
    _pin_dirs(monkeypatch, [d])
    snap = cc.freshest_claude_oauth(now_fn=lambda: NOW_S)
    assert snap.status == "valid"
    assert snap.access_token == _TEST_OAT
    assert snap.subscription_type == "max"
    assert snap.config_dir == d


def test_valid_profile_login_beats_expired_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE 2026-07-10 shape: ~/.claude expired in place, the profile-manager
    dir holds the real, freshly-refreshed login — the login must win."""
    default = tmp_path / "dot-claude"
    profile = tmp_path / "profile"
    _write_credentials(default, expires_at_ms=(NOW_S - 48 * 3600.0) * 1000.0)
    _write_credentials(
        profile, token=_TEST_OAT_2, expires_at_ms=(NOW_S + 4 * 3600.0) * 1000.0
    )
    _pin_dirs(monkeypatch, [default, profile])
    snap = cc.freshest_claude_oauth(now_fn=lambda: NOW_S)
    assert snap.status == "valid"
    assert snap.access_token == _TEST_OAT_2
    assert snap.config_dir == profile


def test_freshest_of_two_valid_logins_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    older = tmp_path / "older"
    fresher = tmp_path / "fresher"
    _write_credentials(older, expires_at_ms=(NOW_S + 600.0) * 1000.0)
    _write_credentials(
        fresher, token=_TEST_OAT_2, expires_at_ms=(NOW_S + 7200.0) * 1000.0
    )
    _pin_dirs(monkeypatch, [older, fresher])
    snap = cc.freshest_claude_oauth(now_fn=lambda: NOW_S)
    assert snap.access_token == _TEST_OAT_2
    assert snap.config_dir == fresher


def test_all_expired_reports_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_credentials(
        a, expires_at_ms=(NOW_S - 3600.0) * 1000.0, subscription_type="max"
    )
    _write_credentials(b, expires_at_ms=(NOW_S - 7200.0) * 1000.0)
    _pin_dirs(monkeypatch, [a, b])
    snap = cc.freshest_claude_oauth(now_fn=lambda: NOW_S)
    assert snap.status == "expired"
    assert snap.access_token is None
    # The least-stale candidate is the reference (its tier feeds the UI).
    assert snap.config_dir == a
    assert snap.subscription_type == "max"


def test_no_readable_bearer_reports_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_dirs(monkeypatch, [tmp_path / "missing"])
    snap = cc.freshest_claude_oauth(now_fn=lambda: NOW_S)
    assert snap.status == "absent"
    assert snap.access_token is None
    assert snap.config_dir is None


def test_classic_api_key_in_bearer_slot_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = tmp_path / "d"
    _write_credentials(
        d, token=_TEST_CLASSIC_KEY, expires_at_ms=(NOW_S + 3600.0) * 1000.0
    )
    _pin_dirs(monkeypatch, [d])
    assert cc.freshest_claude_oauth(now_fn=lambda: NOW_S).status == "absent"


def test_missing_expiry_stays_fail_open_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Older credential shapes without expiresAt keep working (back-compat)."""
    d = tmp_path / "d"
    _write_credentials(d)
    _pin_dirs(monkeypatch, [d])
    snap = cc.freshest_claude_oauth(now_fn=lambda: NOW_S)
    assert snap.status == "valid"
    assert snap.access_token == _TEST_OAT


def test_token_expiring_within_slack_is_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bearer dying seconds from now would 401 mid-mission — never live."""
    d = tmp_path / "d"
    _write_credentials(d, expires_at_ms=(NOW_S + 10.0) * 1000.0)
    _pin_dirs(monkeypatch, [d])
    assert cc.freshest_claude_oauth(now_fn=lambda: NOW_S).status == "expired"


def test_garbage_file_is_tolerated_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = tmp_path / "d"
    d.mkdir()
    (d / ".credentials.json").write_text("{not json", encoding="utf-8")
    _pin_dirs(monkeypatch, [d])
    assert cc.freshest_claude_oauth(now_fn=lambda: NOW_S).status == "absent"


# -- claude_config_dirs --------------------------------------------------


def test_config_dirs_env_override_comes_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env_dir = tmp_path / "pinned-profile"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(env_dir))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    dirs = cc.claude_config_dirs()
    assert dirs[0] == env_dir
    assert home / ".claude" in dirs


def test_config_dirs_include_profile_manager_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    profile = home / ".bridgespace" / "ai-profiles" / "claude" / "prof_x"
    profile.mkdir(parents=True)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    dirs = cc.claude_config_dirs()
    assert dirs[0] == home / ".claude"
    assert profile in dirs


def test_config_dirs_deduplicate_env_and_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    dirs = cc.claude_config_dirs()
    assert dirs.count(home / ".claude") == 1
