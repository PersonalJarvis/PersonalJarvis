"""set_telegram_enabled writes the nested [integrations.telegram] enabled flag.

A wrong key or a broken nested-table write would break boot (config-drift bug
class), so this exercises the real tomlkit round-trip against a temp file.
"""

import tomllib

import pytest

from jarvis.core.config_writer import (
    add_telegram_allowed_user_id,
    set_telegram_enabled,
    set_telegram_pairing,
)


def test_creates_nested_table_when_absent(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text('[brain]\nprimary = "gemini"\n', encoding="utf-8")

    set_telegram_enabled(True, path=cfg)

    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["integrations"]["telegram"]["enabled"] is True
    # untouched sections survive
    assert data["brain"]["primary"] == "gemini"


def test_toggles_existing_flag_and_preserves_siblings(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text(
        "[integrations.telegram]\n"
        "enabled = true\n"
        'chat_id = "123"\n',
        encoding="utf-8",
    )

    set_telegram_enabled(False, path=cfg)

    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["integrations"]["telegram"]["enabled"] is False
    assert data["integrations"]["telegram"]["chat_id"] == "123"


def test_creates_config_when_missing(tmp_path):
    # M1 (headless VPS): in-app writers create the config if absent, not raise.
    p = tmp_path / "absent.toml"
    set_telegram_enabled(True, path=p)
    assert p.exists()


def test_add_allowed_user_id_creates_list(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text("[integrations.telegram]\nenabled = true\n", encoding="utf-8")

    add_telegram_allowed_user_id(12345, path=cfg)

    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["integrations"]["telegram"]["allowed_user_ids"] == [12345]
    assert data["integrations"]["telegram"]["enabled"] is True


def test_add_allowed_user_id_is_idempotent(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text(
        "[integrations.telegram]\nallowed_user_ids = [12345]\n",
        encoding="utf-8",
    )

    add_telegram_allowed_user_id(12345, path=cfg)
    add_telegram_allowed_user_id(67890, path=cfg)

    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["integrations"]["telegram"]["allowed_user_ids"] == [12345, 67890]


def test_set_telegram_pairing_writes_flag(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text("[integrations.telegram]\nenabled = true\n", encoding="utf-8")

    set_telegram_pairing(False, path=cfg)

    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["integrations"]["telegram"]["pair_on_first_private_message"] is False
    assert data["integrations"]["telegram"]["enabled"] is True
