"""Downloads and Pictures resolve to the folder the OS names, on every OS."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jarvis.platform import user_dirs


@pytest.fixture
def linux_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    def write(lines: str) -> Path:
        (home / ".config" / "user-dirs.dirs").write_text(lines, encoding="utf-8")
        return home

    return write


def test_a_spanish_linux_desktop_saves_into_descargas(linux_home) -> None:
    home = linux_home('XDG_DOWNLOAD_DIR="$HOME/Descargas"\nXDG_PICTURES_DIR="$HOME/Imágenes"\n')
    assert user_dirs.downloads_dir() == home / "Descargas"
    assert user_dirs.pictures_dir() == home / "Imágenes"


def test_a_folder_pointed_at_home_falls_back(linux_home) -> None:
    home = linux_home('XDG_DOWNLOAD_DIR="$HOME/"\nXDG_PICTURES_DIR="$HOME"\n')
    assert user_dirs.downloads_dir() == home / "Downloads"
    # Pictures keeps its old meaning: a removed folder means "none".
    assert user_dirs.pictures_dir() is None


def test_without_user_dirs_the_plain_folders_are_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nowhere"))
    assert user_dirs.downloads_dir() == tmp_path / "Downloads"
    assert user_dirs.pictures_dir() == tmp_path / "Pictures"


def test_macos_uses_the_home_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert user_dirs.downloads_dir() == tmp_path / "Downloads"
    assert user_dirs.pictures_dir() == tmp_path / "Pictures"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows known-folder API")
def test_windows_asks_the_known_folder_api() -> None:
    downloads = user_dirs.downloads_dir()
    assert downloads.is_absolute()
    assert user_dirs.windows_known_folder(user_dirs._DOWNLOADS_ID) == downloads  # noqa: SLF001
