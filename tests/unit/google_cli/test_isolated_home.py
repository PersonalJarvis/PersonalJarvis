"""Isolated CLI-home builder for the agy/gemini worker.

The worker redirects HOME to a hook/mcp-free isolated home and mirrors only the
OAuth *login material* into it. Regression focus (2026-06-26): agy stores its
token under ``antigravity-cli/antigravity-oauth-token``, NOT as
``oauth_creds.json`` — mirroring only the latter left the worker logged out
under the redirected HOME even though the user was signed in.
"""
from __future__ import annotations

import json
import os

from jarvis.google_cli.isolated_home import ensure_isolated_home


def _iso(real_dir, dest_root, model="gemini-3.5-flash"):
    return ensure_isolated_home(real_dir=str(real_dir), dest_root=str(dest_root), model=model)


def test_mirrors_gemini_oauth_creds(tmp_path):
    real = tmp_path / "real" / ".gemini"
    real.mkdir(parents=True)
    (real / "oauth_creds.json").write_text(json.dumps({"access_token": "x"}))
    (real / "google_accounts.json").write_text(json.dumps({"active": "u@e.com"}))
    dest = tmp_path / "iso"
    out = _iso(real, dest)
    assert out == str(dest)
    assert (dest / ".gemini" / "oauth_creds.json").is_file()
    assert (dest / ".gemini" / "google_accounts.json").is_file()
    # settings.json is rewritten minimal (no hooks / mcpServers).
    settings = json.loads((dest / ".gemini" / "settings.json").read_text())
    assert "hooks" not in settings and "mcpServers" not in settings


def test_mirrors_agy_token_under_subdir(tmp_path):
    # The bug: agy's real login lives under antigravity-cli/, so the worker home
    # must mirror it there, preserving the subdir, or agy runs logged out.
    real = tmp_path / "real" / ".gemini"
    (real / "antigravity-cli").mkdir(parents=True)
    (real / "antigravity-cli" / "antigravity-oauth-token").write_text(
        json.dumps({"auth_method": "personal", "token": {"access_token": "x"}})
    )
    (real / "antigravity-cli" / "installation_id").write_text("abc123")
    (real / "google_accounts.json").write_text(json.dumps({"active": "u@e.com"}))
    dest = tmp_path / "iso"
    _iso(real, dest)
    mirrored = dest / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    assert mirrored.is_file()
    assert json.loads(mirrored.read_text())["auth_method"] == "personal"


def test_logout_drops_mirrored_agy_token(tmp_path):
    # First a logged-in mirror, then the real login vanishes (logout) → the
    # isolated home must drop the stale agy token so agy logs out there too.
    real = tmp_path / "real" / ".gemini"
    (real / "antigravity-cli").mkdir(parents=True)
    token = real / "antigravity-cli" / "antigravity-oauth-token"
    token.write_text(json.dumps({"token": {"access_token": "x"}}))
    dest = tmp_path / "iso"
    _iso(real, dest)
    assert (dest / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").is_file()

    token.unlink()  # the user logged out
    _iso(real, dest)
    assert not (dest / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").is_file()


def test_empty_token_is_not_a_login(tmp_path):
    real = tmp_path / "real" / ".gemini"
    (real / "antigravity-cli").mkdir(parents=True)
    (real / "antigravity-cli" / "antigravity-oauth-token").write_text("")  # 0 bytes
    dest = tmp_path / "iso"
    _iso(real, dest)
    assert not (dest / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").is_file()


def test_resync_on_token_refresh(tmp_path):
    # A fresh login (token mtime changes) must re-copy; an unchanged token must
    # not clobber agy's in-home refreshed token (the marker guards that).
    real = tmp_path / "real" / ".gemini"
    real.mkdir(parents=True)
    creds = real / "oauth_creds.json"
    creds.write_text(json.dumps({"access_token": "v1"}))
    dest = tmp_path / "iso"
    _iso(real, dest)
    mirrored = dest / ".gemini" / "oauth_creds.json"
    assert json.loads(mirrored.read_text())["access_token"] == "v1"

    # Simulate agy refreshing its own copy inside the home; an unchanged source
    # mtime must NOT overwrite it.
    mirrored.write_text(json.dumps({"access_token": "in-home-refreshed"}))
    os.utime(creds, (creds.stat().st_atime, creds.stat().st_mtime))  # mtime unchanged
    _iso(real, dest)
    assert json.loads(mirrored.read_text())["access_token"] == "in-home-refreshed"

    # A real new login (newer mtime) re-syncs.
    creds.write_text(json.dumps({"access_token": "v2"}))
    new_mtime = creds.stat().st_mtime + 10
    os.utime(creds, (new_mtime, new_mtime))
    _iso(real, dest)
    assert json.loads(mirrored.read_text())["access_token"] == "v2"
