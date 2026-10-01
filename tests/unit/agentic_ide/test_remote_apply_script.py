"""The server-side script that sets up a copied folder never runs in the home folder.

`_APPLY` runs on the connected computer, in a shell that starts in the home
folder. It used to open with `mkdir -p "$DEST" && cd "$DEST"`, and `set -e`
does not stop on a failure inside an && list — so a mkdir that failed left the
script in the home folder, where it ran `git init` and a forced checkout. On a
"remote" computer that was the user's own PC, that stray repository made every
git tool treat the whole user profile as one project.

These run the real script in bash (Git Bash on Windows) against a throwaway
home folder; nothing reaches a server.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.agentic_ide.remote import _APPLY
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS


def _bash() -> str | None:
    """A bash that understands this OS's paths — Git Bash on Windows, never WSL's."""
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git is None:
        return None
    for root in Path(git).resolve().parents[1:3]:
        candidate = root / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


BASH = _bash()

pytestmark = pytest.mark.skipif(
    BASH is None or shutil.which("git") is None, reason="needs bash and git"
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    return folder.resolve()


def _run_apply(home: Path, dest: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    script = _APPLY.format(
        dest=shlex.quote(dest),
        bundle=shlex.quote((home / "missing.bundle").as_posix()),
        ref=shlex.quote("refs/jarvis/offload/test"),
        base=shlex.quote(""),
        branch=shlex.quote(""),
        snap=shlex.quote("0" * 40),
    )
    return subprocess.run(
        [BASH, "-s"],
        input=script,
        cwd=home,  # an SSH session starts in the home folder
        env={**os.environ, "HOME": str(home), "GIT_CEILING_DIRECTORIES": str(home.parent)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


def test_a_folder_that_cannot_be_created_stops_the_script(home: Path) -> None:
    blocker = home / "blocker"
    blocker.write_text("a file where the folder should go", encoding="utf-8")

    result = _run_apply(home, (blocker / "workspace").as_posix())

    assert result.returncode != 0
    assert not (home / ".git").exists(), "the script set up a repository in the home folder"


def test_the_home_folder_itself_is_refused(home: Path) -> None:
    result = _run_apply(home, home.as_posix())

    assert result.returncode != 0
    assert "home folder" in result.stderr
    assert not (home / ".git").exists()


def test_a_normal_folder_still_gets_its_repository(home: Path) -> None:
    dest = home / "jarvis-workspaces" / "app-abc123"

    # Fails later on the missing bundle; what matters is where it got to.
    _run_apply(home, dest.as_posix())

    assert (dest / ".git").is_dir()
    assert not (home / ".git").exists()
