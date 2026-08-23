"""``load_config`` under a non-default instance: ports shift, the memory data
dir follows the instance, and the default instance passes through untouched.
Plus the two store paths that used to assume the data dir is *named* ``data``.
"""
from __future__ import annotations

from pathlib import Path

from jarvis.core import config as cfg
from jarvis.core.instance import DEV_PORT_OFFSET, INSTANCE_ENV_VAR
from jarvis.state.chat_store import default_chats_db_path


def _toml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "jarvis.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_default_instance_keeps_configured_ports_and_data_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(INSTANCE_ENV_VAR, raising=False)
    path = _toml(tmp_path, '[ui]\nadmin_api_port = 50000\n[memory]\ndata_dir = "./data"\n')
    loaded = cfg.load_config(path)
    assert loaded.ui.admin_api_port == 50000
    assert loaded.memory.data_dir == "./data"


def test_dev_instance_offsets_every_port_from_the_configured_base(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(INSTANCE_ENV_VAR, "dev")
    path = _toml(tmp_path, "[ui]\nadmin_api_port = 50000\n[mcp_server]\nhttp_port = 60000\n")
    loaded = cfg.load_config(path)
    assert loaded.ui.admin_api_port == 50000 + DEV_PORT_OFFSET
    assert loaded.mcp_server.http_port == 60000 + DEV_PORT_OFFSET
    assert loaded.telemetry.metrics_port == 9090 + DEV_PORT_OFFSET


def test_dev_instance_offsets_the_packaged_defaults_too(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(INSTANCE_ENV_VAR, "dev")
    loaded = cfg.load_config(_toml(tmp_path, ""))
    assert loaded.ui.admin_api_port == 47821 + DEV_PORT_OFFSET


def test_dev_instance_pins_memory_data_dir_to_its_own_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(INSTANCE_ENV_VAR, "dev")
    loaded = cfg.load_config(_toml(tmp_path, '[memory]\ndata_dir = "./data"\n'))
    # DATA_DIR is bound at import (the launcher pins the instance before that);
    # the override always follows the module's resolved DATA_DIR.
    assert loaded.memory.data_dir == str(cfg.DATA_DIR)


def test_env_override_is_the_base_the_dev_port_offsets_from(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(INSTANCE_ENV_VAR, "dev")
    monkeypatch.setenv("JARVIS__UI__ADMIN_API_PORT", "51000")
    loaded = cfg.load_config(_toml(tmp_path, "[ui]\nadmin_api_port = 50000\n"))
    assert loaded.ui.admin_api_port == 51000 + DEV_PORT_OFFSET


def test_chats_db_lives_inside_the_memory_data_dir() -> None:
    # Default layout unchanged …
    assert default_chats_db_path("./data") == Path("./data") / "chats.db"
    # … and a differently named data dir no longer leaks into ``../data``.
    assert default_chats_db_path("/x/data-dev") == Path("/x/data-dev") / "chats.db"
