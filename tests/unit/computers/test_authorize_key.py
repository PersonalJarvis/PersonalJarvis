"""Planting the app's key must never damage the keys already on the server."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.computers.ssh import authorize_key_command
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(SH is None, reason="needs a POSIX shell")

NEW_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAInew personal-jarvis@test"
OLD_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIold"


def _run(home: Path) -> None:
    subprocess.run(  # noqa: S603 — fixed argv
        [str(SH), "-c", authorize_key_command(NEW_KEY)],
        env={"HOME": str(home), "PATH": str(Path(str(SH)).parent)},
        check=True,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


def _lines(home: Path) -> list[str]:
    return (home / ".ssh" / "authorized_keys").read_text(encoding="utf-8").splitlines()


def test_a_last_line_without_newline_is_not_glued_to_the_new_key(tmp_path: Path) -> None:
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "authorized_keys").write_bytes(OLD_KEY.encode())  # no trailing newline

    _run(tmp_path)

    assert _lines(tmp_path) == [OLD_KEY, NEW_KEY]


def test_planting_twice_adds_the_key_once(tmp_path: Path) -> None:
    _run(tmp_path)
    _run(tmp_path)

    assert _lines(tmp_path) == [NEW_KEY]
