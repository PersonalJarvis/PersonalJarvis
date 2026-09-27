"""AppImages keep default memory outside their read-only mounted resources."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.core.config import load_config
from jarvis.state.chat_store import default_chats_db_path
from scripts.ci.check_posix_native_upgrade import _write_fixture_config


@pytest.mark.parametrize(
    ("platform", "appimage", "expected_redirect"),
    [
        ("win32", False, False),
        ("darwin", False, False),
        ("linux", False, False),
        ("linux", True, True),
    ],
)
def test_only_appimage_redirects_frozen_default_memory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    platform: str,
    appimage: bool,
    expected_redirect: bool,
) -> None:
    config = tmp_path / "jarvis.toml"
    config.write_text("[ui]\nadmin_api_port = 54321\n", encoding="utf-8")
    before = config.read_bytes()
    data = tmp_path / "writable" / "data"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data))
    if appimage:
        monkeypatch.setenv("APPIMAGE", str(tmp_path / "PersonalJarvis.AppImage"))
    else:
        monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("JARVIS__MEMORY__DATA_DIR", raising=False)

    loaded = load_config(config)

    expected_data = data.resolve() if expected_redirect else Path("./data")
    assert Path(loaded.memory.data_dir) == expected_data
    assert default_chats_db_path(loaded.memory.data_dir) == expected_data / "chats.db"
    assert config.read_bytes() == before


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_frozen_custom_memory_path_survives_runtime_data_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str
) -> None:
    config = tmp_path / "jarvis.toml"
    custom = tmp_path / "custom-memory"
    config.write_text(f'[memory]\ndata_dir = "{custom.as_posix()}"\n', encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "runtime-data"))
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "PersonalJarvis.AppImage"))
    monkeypatch.delenv("JARVIS__MEMORY__DATA_DIR", raising=False)

    loaded = load_config(config)

    assert Path(loaded.memory.data_dir) == custom


def test_legacy_upgrade_fixture_has_absolute_memory_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "jarvis.toml"
    data = tmp_path / "data"
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("JARVIS__MEMORY__DATA_DIR", raising=False)

    _write_fixture_config(config, 54321, data)
    loaded = load_config(config)

    assert loaded.ui.admin_api_port == 54321
    assert Path(loaded.memory.data_dir) == data
    assert default_chats_db_path(loaded.memory.data_dir).is_absolute()


def test_appimage_does_not_correct_invalid_memory_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "jarvis.toml"
    config.write_text('memory = "invalid"\n', encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "PersonalJarvis.AppImage"))
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "runtime-data"))

    with pytest.raises(ValidationError):
        load_config(config)
