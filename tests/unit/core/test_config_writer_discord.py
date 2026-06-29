"""add_discord_allowed_user_id writes the nested [integrations.discord] list.

Mirrors test_config_writer_telegram.py: a wrong key or a broken nested-table
write would break boot (config-drift bug class), so this exercises the real
tomlkit round-trip against a temp file.
"""

import tomllib

import pytest

from jarvis.core.config_writer import (
    add_discord_allowed_user_id,
    set_discord_enabled,
    set_discord_pairing,
)


def test_add_allowed_user_id_creates_nested_table_and_list(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text('[brain]\nprimary = "gemini"\n', encoding="utf-8")

    add_discord_allowed_user_id(12345, path=cfg)

    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["integrations"]["discord"]["allowed_user_ids"] == [12345]
    # untouched sections survive
    assert data["brain"]["primary"] == "gemini"


def test_add_allowed_user_id_is_idempotent_and_appends(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text(
        "[integrations.discord]\nallowed_user_ids = [12345]\n",
        encoding="utf-8",
    )

    add_discord_allowed_user_id(12345, path=cfg)  # already present → no dup
    add_discord_allowed_user_id(67890, path=cfg)

    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["integrations"]["discord"]["allowed_user_ids"] == [12345, 67890]


def test_creates_config_when_missing(tmp_path):
    # M1 (headless VPS): in-app writers create the config if absent, not raise.
    p = tmp_path / "absent.toml"
    add_discord_allowed_user_id(1, path=p)
    assert p.exists()


def test_set_discord_enabled_creates_nested_table(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text('[brain]\nprimary = "gemini"\n', encoding="utf-8")

    set_discord_enabled(True, path=cfg)

    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["integrations"]["discord"]["enabled"] is True
    assert data["brain"]["primary"] == "gemini"


def test_set_discord_enabled_toggles_and_preserves_siblings(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text(
        "[integrations.discord]\nenabled = true\nallowed_user_ids = [42]\n",
        encoding="utf-8",
    )

    set_discord_enabled(False, path=cfg)

    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["integrations"]["discord"]["enabled"] is False
    assert data["integrations"]["discord"]["allowed_user_ids"] == [42]


def test_set_discord_pairing_writes_flag(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text("[integrations.discord]\nenabled = true\n", encoding="utf-8")

    set_discord_pairing(False, path=cfg)

    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["integrations"]["discord"]["pair_on_first_dm"] is False
    assert data["integrations"]["discord"]["enabled"] is True


def test_set_discord_enabled_creates_config_when_missing(tmp_path):
    p = tmp_path / "absent.toml"
    set_discord_enabled(True, path=p)
    assert p.exists()
