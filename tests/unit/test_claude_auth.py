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

from jarvis import claude_auth
from jarvis.claude_auth import (
    ClaudeAuthService,
    _account_from_claude_json,
    _oauth_from_credentials,
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
    """Build a service whose seams point at temp files / a stubbed binary."""
    creds_path = tmp_path / ".credentials.json"
    claude_json_path = tmp_path / ".claude.json"
    if creds is not None:
        creds_path.write_text(json.dumps(creds), encoding="utf-8")
    if claude_json is not None:
        claude_json_path.write_text(json.dumps(claude_json), encoding="utf-8")

    svc = ClaudeAuthService(api_key_present=api_key_present)
    monkeypatch.setattr(svc, "_resolve_binary", lambda: binary)
    monkeypatch.setattr(svc, "_probe_version", lambda _b: "claude 1.2.3")
    monkeypatch.setattr(svc, "_credentials_path", lambda: creds_path)
    monkeypatch.setattr(svc, "_claude_json_path", lambda: claude_json_path)
    return svc


# -- pure helpers -------------------------------------------------------


def test_oauth_from_credentials_detects_subscription() -> None:
    creds = {"claudeAiOauth": {"accessToken": "sk-ant-oat01-abc", "subscriptionType": "max"}}
    connected, sub = _oauth_from_credentials(creds)
    assert connected is True
    assert sub == "max"


def test_oauth_from_credentials_rejects_api_key_token() -> None:
    # A classic API key in the bearer slot is NOT a subscription login.
    creds = {"claudeAiOauth": {"accessToken": "sk-ant-api03-abc"}}
    connected, sub = _oauth_from_credentials(creds)
    assert connected is False
    assert sub is None


@pytest.mark.parametrize("bad", [None, {}, {"claudeAiOauth": "nope"}, {"claudeAiOauth": {}}])
def test_oauth_from_credentials_tolerates_garbage(bad) -> None:
    assert _oauth_from_credentials(bad) == (False, None)


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
