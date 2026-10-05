"""Dragged appshots land in a folder only this user can read (shared /tmp on Linux)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from jarvis.appshot import dragfile

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Windows %TEMP% is per user already")


@pytest.fixture
def temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(dragfile.tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


def test_the_folder_carries_the_user_and_is_private(temp: Path) -> None:
    path = dragfile.write_drag_file(b"\x89PNG\r\n\x1a\n")

    assert path is not None
    assert path.parent == temp / f"jarvis-appshots-{os.getuid()}"
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_world_readable_folder_of_ours_is_tightened(temp: Path) -> None:
    folder = temp / f"jarvis-appshots-{os.getuid()}"
    folder.mkdir(mode=0o755)
    os.chmod(folder, 0o755)  # noqa: S103 - the loose folder this test tightens

    assert dragfile.prepare_drag_folder() == folder
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_a_planted_symlink_is_refused(temp: Path) -> None:
    elsewhere = temp / "attacker"
    elsewhere.mkdir()
    (temp / f"jarvis-appshots-{os.getuid()}").symlink_to(elsewhere)

    assert dragfile.write_drag_file(b"\x89PNG\r\n\x1a\n") is None
    assert list(elsewhere.iterdir()) == []
