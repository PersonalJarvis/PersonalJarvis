"""Status derivation for the Google CLI auth service (no real binary/network)."""
from __future__ import annotations

import json

import jarvis.google_cli.auth_service as auth_mod
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
    gem.mkdir()  # no oauth_creds.json, no agy token
    monkeypatch.setenv("GEMINI_HOME", str(gem))
    svc = GoogleCliAuthService()
    svc._resolve = lambda: GoogleCli(  # type: ignore[method-assign]
        kind="agy", argv_prefix=["agy"], version=None
    )
    st = svc.status()
    assert st.installed and not st.connected
    assert st.cli_kind == "agy"


def _seed_agy_home(tmp_path):
    """A real-world agy login: the OAuth token lives under ``antigravity-cli/``,
    NOT as ``oauth_creds.json`` (verified live 2026-06-26 with agy 1.0.12)."""
    gem = tmp_path / ".gemini"
    (gem / "antigravity-cli").mkdir(parents=True)
    (gem / "antigravity-cli" / "antigravity-oauth-token").write_text(
        json.dumps({"auth_method": "personal", "token": {"access_token": "x"}})
    )
    # agy shares ~/.gemini for the account + the (clean) selected-type marker.
    (gem / "settings.json").write_text(
        json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}})
    )
    (gem / "google_accounts.json").write_text(json.dumps({"active": "user@example.com"}))
    return gem


def test_status_connected_via_agy_oauth_token(tmp_path, monkeypatch):
    # The reported bug: agy is logged in (token under antigravity-cli/) but the
    # status read the wrong path (~/.gemini/oauth_creds.json) and showed
    # "Installed but not logged in". It must now report connected.
    gem = _seed_agy_home(tmp_path)
    monkeypatch.setenv("GEMINI_HOME", str(gem))
    svc = GoogleCliAuthService()
    svc._resolve = lambda: GoogleCli(  # type: ignore[method-assign]
        kind="agy", argv_prefix=["agy.exe"], version="1.0.12"
    )
    st = svc.status()
    assert st.installed and st.connected
    assert st.mode == "oauth-personal"
    assert st.cli_kind == "agy"
    assert st.user_email == "user@example.com"


def test_status_agy_empty_token_file_is_not_connected(tmp_path, monkeypatch):
    # A zero-byte token file (interrupted login) must not read as connected.
    gem = tmp_path / ".gemini"
    (gem / "antigravity-cli").mkdir(parents=True)
    (gem / "antigravity-cli" / "antigravity-oauth-token").write_text("")
    monkeypatch.setenv("GEMINI_HOME", str(gem))
    svc = GoogleCliAuthService()
    svc._resolve = lambda: GoogleCli(kind="agy", argv_prefix=["agy"])  # type: ignore[method-assign]
    assert not svc.status().connected


def test_logout_removes_agy_token(tmp_path, monkeypatch):
    # Disconnect must clear the agy token too, not only oauth_creds.json — else
    # the Disconnect button is a no-op for an agy login.
    gem = _seed_agy_home(tmp_path)
    monkeypatch.setenv("GEMINI_HOME", str(gem))
    svc = GoogleCliAuthService()
    svc._resolve = lambda: GoogleCli(kind="agy", argv_prefix=["agy.exe"])  # type: ignore[method-assign]
    ok, err = svc.logout_blocking()
    assert ok and err is None
    assert not (gem / "antigravity-cli" / "antigravity-oauth-token").is_file()
    assert not svc.status().connected


def test_start_login_uses_bare_agy_not_login_subcommand(monkeypatch):
    # agy has NO `login` subcommand (verified 2026-06-21: `agy login` hangs
    # forever). The login is an interactive bare run, so start_login must NOT
    # append "login" — that would spawn a hung process behind the Connect button.
    svc = GoogleCliAuthService()
    svc._resolve = lambda: GoogleCli(kind="agy", argv_prefix=["agy.exe"])  # type: ignore[method-assign]
    captured: dict = {}

    def _fake_popen(argv, **kw):
        captured["argv"] = list(argv)
        return object()

    monkeypatch.setattr(auth_mod.subprocess, "Popen", _fake_popen)
    svc.start_login()
    assert captured["argv"] == ["agy.exe"]  # bare binary, no "login"


def test_logout_removes_creds_without_calling_agy_logout(tmp_path, monkeypatch):
    # agy has NO `logout` subcommand either; removing the on-disk OAuth creds IS
    # the disconnect. Trying `agy logout` would hang/time out for nothing.
    gem = _seed_gemini_home(tmp_path)
    monkeypatch.setenv("GEMINI_HOME", str(gem))
    svc = GoogleCliAuthService()
    svc._resolve = lambda: GoogleCli(kind="agy", argv_prefix=["agy.exe"])  # type: ignore[method-assign]

    def _no_run(*a, **k):
        raise AssertionError("agy logout must not be invoked (no such subcommand)")

    monkeypatch.setattr(auth_mod.subprocess, "run", _no_run)
    ok, err = svc.logout_blocking()
    assert ok and err is None
    assert not (gem / "oauth_creds.json").is_file()  # creds actually removed
