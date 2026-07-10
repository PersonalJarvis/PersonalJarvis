"""Unit tests for jarvis.claude_auth.ClaudeAuthService.

Exercises the real credential parsing (subscription OAuth vs API key vs not
connected) and the display-safe account/subscription surfacing against temp
files, with the binary discovery + version probe stubbed so the suite never
depends on a real ``claude`` install or the user's real credentials.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis import claude_auth, claude_credentials
from jarvis.claude_auth import (
    ClaudeAuthService,
    _account_from_claude_json,
    _subscription_label,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    claude_auth.clear_version_cache()


def _service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    creds: dict | None,
    claude_json: dict | None,
    binary: str | None = "/usr/bin/claude",
    api_key_present: bool = False,
) -> ClaudeAuthService:
    """Build a service whose seams point at temp files / a stubbed binary.

    ``tmp_path`` acts as the single candidate Claude config dir (pinned via
    ``claude_credentials.claude_config_dirs``), so the OAuth snapshot reads
    the temp credentials file — hermetic against the host's real logins.
    """
    creds_path = tmp_path / ".credentials.json"
    claude_json_path = tmp_path / ".claude.json"
    if creds is not None:
        creds_path.write_text(json.dumps(creds), encoding="utf-8")
    if claude_json is not None:
        claude_json_path.write_text(json.dumps(claude_json), encoding="utf-8")

    svc = ClaudeAuthService(api_key_present=api_key_present)
    monkeypatch.setattr(
        claude_credentials, "claude_config_dirs", lambda: [tmp_path]
    )
    monkeypatch.setattr(svc, "_resolve_binary", lambda: binary)
    monkeypatch.setattr(svc, "_probe_version", lambda _b: "claude 1.2.3")
    monkeypatch.setattr(svc, "_credentials_path", lambda: creds_path)
    monkeypatch.setattr(svc, "_claude_json_path", lambda: claude_json_path)
    return svc


# -- pure helpers -------------------------------------------------------
# (credential parsing/expiry/multi-dir selection is covered by
# tests/unit/test_claude_credentials.py — the shared locator owns it now)


def test_account_from_claude_json_reads_email() -> None:
    data = {"oauthAccount": {"emailAddress": "ruben@example.com", "displayName": "Ruben"}}
    assert _account_from_claude_json(data) == ("ruben@example.com", "Ruben")


@pytest.mark.parametrize("bad", [None, {}, {"oauthAccount": 5}])
def test_account_from_claude_json_tolerates_garbage(bad) -> None:
    assert _account_from_claude_json(bad) == (None, None)


def test_subscription_label_maps_known_tiers() -> None:
    assert _subscription_label("max") == "Claude Max"
    assert _subscription_label("pro") == "Claude Pro"
    assert _subscription_label(None) == "Claude subscription"


# -- status() integration ----------------------------------------------


def test_status_subscription_with_email(tmp_path, monkeypatch) -> None:
    svc = _service(
        tmp_path,
        monkeypatch,
        creds={"claudeAiOauth": {"accessToken": "sk-ant-oat01-x", "subscriptionType": "max"}},
        claude_json={"oauthAccount": {"emailAddress": "ruben@example.com"}},
    )
    st = svc.status()
    assert st.installed is True
    assert st.connected is True
    assert st.mode == "subscription"
    assert st.user_email == "ruben@example.com"
    assert st.subscription_type == "max"
    assert st.account_label == "Claude Max"
    assert "ruben@example.com" in st.message


def test_status_api_key_when_no_oauth(tmp_path, monkeypatch) -> None:
    svc = _service(
        tmp_path,
        monkeypatch,
        creds=None,
        claude_json=None,
        api_key_present=True,
    )
    st = svc.status()
    assert st.connected is True
    assert st.mode == "api_key"
    assert st.user_email is None
    assert st.account_label == "Anthropic API key"


def test_status_not_connected_without_creds_or_key(tmp_path, monkeypatch) -> None:
    svc = _service(tmp_path, monkeypatch, creds=None, claude_json=None)
    st = svc.status()
    assert st.installed is True
    assert st.connected is False
    assert st.mode == "unknown"
    assert st.api_key_present is False


def test_status_expired_subscription_is_not_connected(tmp_path, monkeypatch) -> None:
    """The presence-only check reported 'Connected via Claude Max' for a token
    that had been dead since 02:53 (2026-07-06) — the UI showed green while
    every subagent spawn 401'd. An expired login must be honest and say how
    to fix it."""
    svc = _service(
        tmp_path,
        monkeypatch,
        creds={
            "claudeAiOauth": {
                "accessToken": "sk-ant-oat01-x",
                "subscriptionType": "max",
                "expiresAt": 1.0,  # epoch ms, long past
            }
        },
        claude_json=None,
    )
    st = svc.status()
    assert st.installed is True
    assert st.connected is False
    assert "expired" in st.message.lower()
    assert "/login" in st.message


def test_status_expired_oauth_falls_back_to_api_key(tmp_path, monkeypatch) -> None:
    """Expired subscription login + a configured API key → the key is the
    honest connected surface (it can still authenticate the CLI/API)."""
    svc = _service(
        tmp_path,
        monkeypatch,
        creds={
            "claudeAiOauth": {
                "accessToken": "sk-ant-oat01-x",
                "expiresAt": 1.0,
            }
        },
        claude_json=None,
        api_key_present=True,
    )
    st = svc.status()
    assert st.connected is True
    assert st.mode == "api_key"


def test_status_surfaces_api_key_present_even_under_subscription(
    tmp_path, monkeypatch
) -> None:
    # A user with BOTH a live Claude Max login AND a stored API key: mode stays
    # "subscription" (billed first), but api_key_present must be True so the UI
    # renders the key field in its configured state instead of an empty input.
    svc = _service(
        tmp_path,
        monkeypatch,
        creds={"claudeAiOauth": {"accessToken": "sk-ant-oat01-x", "subscriptionType": "max"}},
        claude_json={"oauthAccount": {"emailAddress": "ruben@example.com"}},
        api_key_present=True,
    )
    st = svc.status()
    assert st.mode == "subscription"
    assert st.api_key_present is True


def test_status_connected_via_fresh_profile_when_default_is_stale(
    tmp_path, monkeypatch
) -> None:
    """2026-07-10 incident: ~/.claude held a login that expired in place while
    the profile manager's config dir (where every interactive session actually
    runs) held a freshly-refreshed one. The card said "subscription login has
    expired" and the Jarvis-Agents banner diverted missions to codex although
    a live login sat on disk. The freshest login must win — including reading
    the account identity from the WINNING dir, not the stale default's."""
    default_dir = tmp_path / "dot-claude"
    profile_dir = tmp_path / "profile"
    for d in (default_dir, profile_dir):
        d.mkdir()
    (default_dir / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "sk-ant-oat01-stale",
                    "subscriptionType": "max",
                    "expiresAt": 1.0,  # epoch ms, long past
                }
            }
        ),
        encoding="utf-8",
    )
    (profile_dir / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "sk-ant-oat01-fresh",
                    "subscriptionType": "max",
                    "expiresAt": 4_102_444_800_000.0,  # epoch ms, far future
                }
            }
        ),
        encoding="utf-8",
    )
    (profile_dir / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"emailAddress": "profile@example.com"}}),
        encoding="utf-8",
    )

    svc = ClaudeAuthService()
    monkeypatch.setattr(
        claude_credentials,
        "claude_config_dirs",
        lambda: [default_dir, profile_dir],
    )
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "/usr/bin/claude")
    monkeypatch.setattr(svc, "_probe_version", lambda _b: "claude 1.2.3")
    st = svc.status()
    assert st.connected is True
    assert st.mode == "subscription"
    assert st.user_email == "profile@example.com"


def test_status_api_key_present_when_not_installed(tmp_path, monkeypatch) -> None:
    # The key field stays "configured" even if the CLI binary is absent — the
    # stored key is independent of the local install.
    svc = _service(
        tmp_path,
        monkeypatch,
        creds=None,
        claude_json=None,
        binary=None,
        api_key_present=True,
    )
    st = svc.status()
    assert st.installed is False
    assert st.api_key_present is True


def test_status_not_installed(tmp_path, monkeypatch) -> None:
    svc = _service(tmp_path, monkeypatch, creds=None, claude_json=None, binary=None)
    st = svc.status()
    assert st.installed is False
    assert st.connected is False
    assert "not installed" in st.message.lower()


def test_to_dict_has_wire_fields(tmp_path, monkeypatch) -> None:
    svc = _service(
        tmp_path,
        monkeypatch,
        creds={"claudeAiOauth": {"accessToken": "sk-ant-oat01-x", "subscriptionType": "max"}},
        claude_json={"oauthAccount": {"emailAddress": "ruben@example.com"}},
    )
    d = svc.status().to_dict()
    for key in (
        "installed",
        "connected",
        "mode",
        "message",
        "user_email",
        "subscription_type",
        "account_label",
        "api_key_present",
    ):
        assert key in d
    # The bearer token is never surfaced.
    assert "accessToken" not in d
    assert "sk-ant-oat" not in json.dumps(d)


def test_status_never_logs_secret(tmp_path, monkeypatch, caplog) -> None:
    svc = _service(
        tmp_path,
        monkeypatch,
        creds={"claudeAiOauth": {"accessToken": "sk-ant-oat01-SECRET", "subscriptionType": "max"}},
        claude_json={"oauthAccount": {"emailAddress": "ruben@example.com"}},
    )
    with caplog.at_level("DEBUG"):
        svc.status()
    assert "sk-ant-oat01-SECRET" not in caplog.text
