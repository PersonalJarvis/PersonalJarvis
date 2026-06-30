"""M1 (open-source AP-22 / headless VPS): in-app config writers (channel toggles,
provider switches, wiki curator) defaulted to /app/jarvis.toml and RAISED
FileNotFoundError when it was absent — so connecting a channel / switching a
provider in the UI 500'd on a fresh headless container. They must instead resolve
the active config path (JARVIS_CONFIG) and create it if missing.
"""
from __future__ import annotations

import tomllib

from jarvis.core import config_writer as cw


def test_set_telegram_enabled_creates_missing_config(tmp_path):
    p = tmp_path / "sub" / "jarvis.toml"  # neither file nor parent exists
    cw.set_telegram_enabled(True, path=p)
    assert p.exists()
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["integrations"]["telegram"]["enabled"] is True


def test_add_telegram_allowed_user_creates_missing_config(tmp_path):
    p = tmp_path / "jarvis.toml"
    cw.add_telegram_allowed_user_id(7911329168, path=p)
    assert p.exists()
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert 7911329168 in data["integrations"]["telegram"]["allowed_user_ids"]


def test_set_discord_enabled_creates_missing_config(tmp_path):
    p = tmp_path / "jarvis.toml"
    cw.set_discord_enabled(True, path=p)
    assert p.exists()
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["integrations"]["discord"]["enabled"] is True
