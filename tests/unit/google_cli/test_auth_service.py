"""Status derivation for the Google CLI auth service (no real binary/network)."""
from __future__ import annotations

import json

from jarvis.google_cli.auth_service import (
    GoogleCliAuthService,
    _derive_google_auth,
)
from jarvis.google_cli.resolver import GoogleCli


def test_derive_oauth_personal():
    settings = {"security": {"auth": {"selectedType": "oauth-personal"}}}
    assert _derive_google_auth(creds_present=True, settings=settings) == (
        True,
        "oauth-personal",
    )


def test_derive_creds_without_type_still_connected():
    assert _derive_google_auth(creds_present=True, settings={}) == (
        True,
        "oauth-personal",
    )


def test_derive_api_key_type_without_creds():
    settings = {"security": {"auth": {"selectedType": "gemini-api-key"}}}
    assert _derive_google_auth(creds_present=False, settings=settings) == (
        True,
        "api_key",
    )


def test_derive_unknown_when_nothing():
    assert _derive_google_auth(creds_present=False, settings={}) == (False, "unknown")


def _seed_gemini_home(tmp_path):
    gem = tmp_path / ".gemini"
    gem.mkdir()
    (gem / "oauth_creds.json").write_text(
        json.dumps({"access_token": "x", "refresh_token": "y"})
    )
    (gem / "settings.json").write_text(
        json.dumps(
            {
                "security": {"auth": {"selectedType": "oauth-personal"}},
                "model": {"name": "gemini-3.1-pro-preview"},
            }
        )
    )
    (gem / "google_accounts.json").write_text(json.dumps({"active": "user@example.com"}))
    return gem


def test_status_connected(tmp_path, monkeypatch):
    gem = _seed_gemini_home(tmp_path)
    monkeypatch.setenv("GEMINI_HOME", str(gem))
    svc = GoogleCliAuthService()
    svc._resolve = lambda: GoogleCli(  # type: ignore[method-assign]
        kind="gemini", argv_prefix=["gemini"], version="0.47.0"
    )
    st = svc.status()
    assert st.installed and st.connected
    assert st.mode == "oauth-personal"
    assert st.cli_kind == "gemini"
    assert st.user_email == "user@example.com"
    assert "google subscription" in st.message.lower()


def test_status_not_installed(monkeypatch):
    svc = GoogleCliAuthService()
    svc._resolve = lambda: None  # type: ignore[method-assign]
    st = svc.status()
    assert not st.installed and not st.connected
    d = st.to_dict()
    assert d["mode"] == "unknown"
    assert d["installed"] is False


def test_status_installed_but_logged_out(tmp_path, monkeypatch):
    gem = tmp_path / ".gemini"
    gem.mkdir()  # no oauth_creds.json
    monkeypatch.setenv("GEMINI_HOME", str(gem))
    svc = GoogleCliAuthService()
    svc._resolve = lambda: GoogleCli(  # type: ignore[method-assign]
        kind="agy", argv_prefix=["agy"], version=None
    )
    st = svc.status()
    assert st.installed and not st.connected
    assert st.cli_kind == "agy"
