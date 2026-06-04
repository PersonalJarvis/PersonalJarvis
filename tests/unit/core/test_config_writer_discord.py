"""add_discord_allowed_user_id writes the nested [integrations.discord] list.

Mirrors test_config_writer_telegram.py: a wrong key or a broken nested-table
write would break boot (config-drift bug class), so this exercises the real
tomlkit round-trip against a temp file.
"""

import tomllib

import pytest

from jarvis.core.config_writer import add_discord_allowed_user_id


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


def test_raises_when_config_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        add_discord_allowed_user_id(1, path=tmp_path / "absent.toml")
